# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from typing import Any

from sunbeam_migrate import exception
from sunbeam_migrate.handlers import base

LOG = logging.getLogger(__name__)


class ZoneHandler(base.BaseMigrationHandler):
    """Handle Designate DNS zone migrations."""

    def get_service_type(self) -> str:
        """Get the service type for this type of resource."""
        return "designate"

    def get_supported_resource_filters(self) -> list[str]:
        """Get a list of supported resource filters.

        These filters can be specified when initiating batch migrations.
        """
        return ["project_id"]

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
        """Migrate the specified DNS zone by copying zone and recordsets."""
        source_zone = self._source_session.dns.get_zone(resource_id)
        if not source_zone:
            raise exception.NotFound(f"DNS zone not found: {resource_id}")

        existing = self._destination_session.dns.find_zone(
            source_zone.name, ignore_missing=True
        )
        if existing:
            LOG.info(
                "Zone %s already exists on destination (id: %s), skipping migration",
                source_zone.name,
                existing.id,
            )
            return existing.id

        # Create zone on destination
        dest_zone = self._create_destination_zone(source_zone)

        # Copy all recordsets from source to destination
        self._copy_recordsets(resource_id, dest_zone.id)

        return dest_zone.id

    def get_source_resource_ids(self, resource_filters: dict[str, str]) -> list[str]:
        """Returns a list of resource ids based on the specified filters.

        Raises an exception if any of the filters are unsupported.
        """
        self._validate_resource_filters(resource_filters)

        query_filters: dict[str, str] = {}
        if "project_id" in resource_filters:
            query_filters["project_id"] = resource_filters["project_id"]

        resource_ids = []
        for resource in self._source_session.dns.zones(**query_filters):
            resource_ids.append(resource.id)

        return resource_ids

    def _delete_resource(self, resource_id: str, openstack_session):
        openstack_session.dns.delete_zone(resource_id, ignore_missing=True)

    def _create_destination_zone(self, source_zone: Any):
        """Create a zone on the destination cloud based on source zone properties."""
        LOG.info("Creating zone %s on destination", source_zone.name)

        fields = [
            "description",
            "email",
            "name",
            "ttl",
            "type",
            "is_shared",
        ]

        zone_attrs = {}
        for field in fields:
            value = getattr(source_zone, field, None)
            if value:
                zone_attrs[field] = value

        dest_zone = self._destination_session.dns.create_zone(**zone_attrs)
        LOG.info(
            "Created zone %s on destination (id: %s)", dest_zone.name, dest_zone.id
        )
        return dest_zone

    def _copy_recordsets(self, source_zone_id: str, dest_zone_id: str):
        """Copy all recordsets from source zone to destination zone."""
        LOG.info(
            "Copying recordsets from source zone %s to destination zone %s",
            source_zone_id,
            dest_zone_id,
        )

        # Get all recordsets from source zone
        source_recordsets = list(
            self._source_session.dns.recordsets(zone=source_zone_id)
        )

        for recordset in source_recordsets:
            # Skip NS and SOA records at the zone apex - these are created automatically
            if recordset.type in ["NS", "SOA"]:
                LOG.debug(
                    "Skipping auto-created recordset: %s (%s)",
                    recordset.name,
                    recordset.type,
                )
                continue

            LOG.info("Copying recordset: %s (%s)", recordset.name, recordset.type)

            fields = [
                "description",
                "name",
                "records",
                "ttl",
                "type",
            ]
            recordset_attrs = {}
            for field in fields:
                value = getattr(recordset, field, None)
                if value:
                    recordset_attrs[field] = value

            try:
                dest_recordset = self._destination_session.dns.create_recordset(
                    zone=dest_zone_id, **recordset_attrs
                )
                LOG.info(
                    "Created recordset: %s (%s)",
                    dest_recordset.name,
                    dest_recordset.type,
                )
            except Exception as e:
                LOG.warning(
                    "Failed to create recordset %s (%s): %s",
                    recordset.name,
                    recordset.type,
                    e,
                )
                continue
