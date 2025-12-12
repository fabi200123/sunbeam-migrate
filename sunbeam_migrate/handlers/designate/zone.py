# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import time
from typing import Any

from sunbeam_migrate import config, exception
from sunbeam_migrate.handlers import base

LOG = logging.getLogger(__name__)
CONF = config.get_config()


class ZoneHandler(base.BaseMigrationHandler):
    """Handle Designate DNS zone migrations."""

    def get_service_type(self) -> str:
        """Get the service type for this type of resource."""
        return "designate"

    def get_supported_resource_filters(self) -> list[str]:
        """Get a list of supported resource filters.

        These filters can be specified when initiating batch migrations.
        """
        return ["owner_id", "name"]

    def get_associated_resource_types(self) -> list[str]:
        """DNS zones have no prerequisite resources."""
        return []

    def get_member_resource_types(self) -> list[str]:
        """DNS zones do not expose member resources via the migration manager."""
        return []

    def perform_individual_migration(
        self,
        resource_id: str,
        migrated_associated_resources: list[base.MigratedResource],
    ) -> str:
        """Migrate the specified DNS zone along with its recordsets."""
        del migrated_associated_resources

        source_zone = self._source_session.dns.get_zone(resource_id)
        if not source_zone:
            raise exception.NotFound(f"DNS zone not found: {resource_id}")

        dest_zone = self._destination_session.dns.find_zone(
            source_zone.name, ignore_missing=True
        )

        if dest_zone:
            LOG.info(
                "Zone %s already exists on destination (id: %s), syncing recordsets",
                source_zone.name,
                dest_zone.id,
            )
            dest_zone_id = dest_zone.id
            self._wait_for_zone_active(dest_zone_id)
        else:
            dest_zone = self._create_destination_zone(source_zone)
            dest_zone_id = dest_zone.id
            self._wait_for_zone_active(dest_zone_id)

        self._sync_recordsets(source_zone, dest_zone_id)
        return dest_zone_id

    def get_source_resource_ids(self, resource_filters: dict[str, str]) -> list[str]:
        """Returns a list of resource ids based on the specified filters.

        Raises an exception if any of the filters are unsupported.
        """
        self._validate_resource_filters(resource_filters)

        query_filters: dict[str, str] = {}
        if "owner_id" in resource_filters:
            query_filters["project_id"] = resource_filters["owner_id"]
        if "name" in resource_filters:
            query_filters["name"] = resource_filters["name"]

        return [zone.id for zone in self._source_session.dns.zones(**query_filters)]

    def _delete_resource(self, resource_id: str, openstack_session):
        openstack_session.dns.delete_zone(resource_id, ignore_missing=True)

    def _create_destination_zone(self, source_zone: Any):
        """Create the destination DNS zone."""
        kwargs: dict[str, Any] = {}
        for field in ["name", "email", "ttl", "description", "type", "masters"]:
            value = getattr(source_zone, field, None)
            if value:
                kwargs[field] = value

        dest_zone = self._destination_session.dns.create_zone(**kwargs)
        LOG.info(
            "Created DNS zone %s on destination (source id: %s)",
            dest_zone.id,
            source_zone.id,
        )
        return dest_zone

    def _sync_recordsets(self, source_zone: Any, dest_zone_id: str):
        """Copy recordsets from the source zone to the destination zone."""
        zone_type = (getattr(source_zone, "type", "PRIMARY") or "PRIMARY").upper()
        if zone_type != "PRIMARY":
            LOG.warning(
                "Source zone %s is of type %s; skipping recordset sync. "
                "Secondary zones replicate records from their masters.",
                source_zone.name,
                zone_type,
            )
            return

        destination_recordsets = self._get_destination_recordset_map(dest_zone_id)

        for recordset in self._source_session.dns.recordsets(source_zone.id):
            if self._should_skip_recordset(recordset, source_zone.name):
                continue

            create_payload = self._build_recordset_payload(
                recordset, include_identity=True
            )
            update_payload = self._build_recordset_payload(
                recordset, include_identity=False
            )

            dest_recordset = destination_recordsets.get(
                (recordset.name, recordset.type)
            )

            if dest_recordset:
                LOG.info(
                    "Updating destination recordset %s (%s) in zone %s",
                    recordset.name,
                    recordset.type,
                    dest_zone_id,
                )
                self._destination_session.dns.update_recordset(
                    dest_zone_id, dest_recordset.id, **update_payload
                )
            else:
                LOG.info(
                    "Creating destination recordset %s (%s) in zone %s",
                    recordset.name,
                    recordset.type,
                    dest_zone_id,
                )
                self._destination_session.dns.create_recordset(
                    dest_zone_id, **create_payload
                )

            # Wait for the recordset we just touched to become ACTIVE, using name/type
            self._wait_for_recordset_active(
                dest_zone_id, recordset.name, recordset.type
            )

    def _wait_for_zone_active(self, zone_id: str):
        """Wait for a DNS zone to become ACTIVE on the destination cloud."""
        start_time = time.time()
        while True:
            zone = self._destination_session.dns.get_zone(zone_id)
            status = (getattr(zone, "status", "") or "").upper()

            if status == "ACTIVE":
                return
            if status == "ERROR":
                raise exception.SunbeamMigrateException(
                    f"Destination DNS zone {zone_id} entered ERROR status."
                )
            if time.time() - start_time > CONF.resource_creation_timeout:
                raise exception.SunbeamMigrateException(
                    f"Timed out waiting for DNS zone {zone_id} to become ACTIVE."
                )
            time.sleep(2)

    def _wait_for_recordset_active(
        self, zone_id: str, recordset_name: str, recordset_type: str
    ):
        """Wait for a DNS recordset (by name/type) to become ACTIVE on destination."""
        start_time = time.time()
        while True:
            target = None
            for candidate in self._destination_session.dns.recordsets(zone_id):
                if (
                    candidate.name == recordset_name
                    and candidate.type == recordset_type
                ):
                    target = candidate
                    break

            if not target:
                if time.time() - start_time > CONF.resource_creation_timeout:
                    raise exception.SunbeamMigrateException(
                        f"Timed out waiting for DNS recordset {recordset_name} "
                        f"({recordset_type}) to be created in zone {zone_id}."
                    )
                time.sleep(2)
                continue

            status = (getattr(target, "status", "") or "").upper()

            if status == "ACTIVE":
                return
            if status == "ERROR":
                raise exception.SunbeamMigrateException(
                    f"Destination DNS recordset {recordset_name} ({recordset_type}) "
                    f"entered ERROR status."
                )
            if time.time() - start_time > CONF.resource_creation_timeout:
                raise exception.SunbeamMigrateException(
                    "Timed out waiting for DNS recordset %s (%s) to become ACTIVE."
                    % (recordset_name, recordset_type)
                )
            time.sleep(2)

    def _get_destination_recordset_map(self, dest_zone_id: str) -> dict[tuple, Any]:
        """Build a map of destination recordsets keyed by (name, type)."""
        recordset_map: dict[tuple, Any] = {}
        for recordset in self._destination_session.dns.recordsets(dest_zone_id):
            recordset_map[(recordset.name, recordset.type)] = recordset
        return recordset_map

    def _should_skip_recordset(self, recordset: Any, zone_name: str) -> bool:
        """Determine whether a recordset should be skipped during migration."""
        if recordset.type == "SOA":
            return True

        apex = zone_name.rstrip(".")
        if recordset.type == "NS" and recordset.name.rstrip(".") == apex:
            return True

        return False

    def _build_recordset_payload(
        self, recordset: Any, include_identity: bool
    ) -> dict[str, Any]:
        """Build payload for creating or updating a recordset."""
        payload: dict[str, Any] = {
            "records": list(recordset.records or []),
        }

        if include_identity:
            payload["name"] = recordset.name
            payload["type"] = recordset.type

        if getattr(recordset, "ttl", None) is not None:
            payload["ttl"] = recordset.ttl
        if getattr(recordset, "description", None):
            payload["description"] = recordset.description
        if getattr(recordset, "priority", None) is not None:
            payload["priority"] = recordset.priority

        return payload

