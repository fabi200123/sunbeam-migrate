# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

from openstack import exceptions as openstack_exc

from sunbeam_migrate import constants, exception
from sunbeam_migrate.handlers import base

LOG = logging.getLogger(__name__)


class RouterHandler(base.BaseMigrationHandler):
    """Handle Neutron router migrations."""

    def get_service_type(self) -> str:
        """Get the service type for this type of resource."""
        return "neutron"

    def get_supported_resource_filters(self) -> list[str]:
        """Get a list of supported resource filters.

        These filters can be specified when initiating batch migrations.
        """
        return ["owner_id"]

    def get_implementation_status(self) -> str:
        """Describe the implementation status."""
        return constants.IMPL_PARTIAL

    # -------------------------------------------------------------------------
    # Associated resources (external gateway)
    # -------------------------------------------------------------------------
    def get_associated_resource_types(self) -> list[str]:
        """Routers depend on their external gateway network and subnet."""
        return ["network", "subnet"]

    def get_associated_resources(self, resource_id: str) -> list[tuple[str, str]]:
        """Return the external network and subnet this router depends on."""
        source_router = self._source_session.network.get_router(resource_id)
        if not source_router:
            raise exception.NotFound(f"Router not found: {resource_id}")

        associated: list[tuple[str, str]] = []
        egi = source_router.external_gateway_info or {}

        net_id = egi.get("network_id")
        if net_id:
            associated.append(("network", net_id))

        for fixed_ip in egi.get("external_fixed_ips", []) or []:
            subnet_id = fixed_ip.get("subnet_id")
            if subnet_id:
                associated.append(("subnet", subnet_id))

        return associated

    # -------------------------------------------------------------------------
    # Member resources (internal interfaces)
    # -------------------------------------------------------------------------
    def get_member_resource_types(self) -> list[str]:
        """Internal router members: their subnets.

        The subnet handler will migrate the corresponding networks as deps.
        """
        return ["subnet"]

    def get_member_resources(self, resource_id: str) -> list[tuple[str, str]]:
        """Return internal subnets connected to this router."""
        source_router = self._source_session.network.get_router(resource_id)
        if not source_router:
            raise exception.NotFound(f"Router not found: {resource_id}")

        member_subnet_ids: set[str] = set()

        INTERNAL_OWNERS_PREFIXES = (
            "network:router_interface",
            "network:router_interface_distributed",
            "network:ha_router_replicated_interface",
        )

        # Fetch all ports whose device_id == router.id
        for port in self._source_session.network.ports(device_id=source_router.id):
            owner = getattr(port, "device_owner", "") or ""
            if not any(owner.startswith(prefix) for prefix in INTERNAL_OWNERS_PREFIXES):
                # Skip gateway ports like 'network:router_gateway', etc.
                continue

            for ip in getattr(port, "fixed_ips", []) or []:
                subnet_id = ip.get("subnet_id")
                if subnet_id:
                    member_subnet_ids.add(subnet_id)

        return [("subnet", sid) for sid in member_subnet_ids]

    # -------------------------------------------------------------------------
    # Router migration itself
    # -------------------------------------------------------------------------
    def perform_individual_migration(
        self,
        resource_id: str,
        migrated_associated_resources: list[tuple[str, str, str]],
    ) -> str:
        """Migrate the specified resource.

        :param resource_id: the resource to be migrated
        :param migrated_associated_resources: list of
            (resource_type, source_id, destination_id) for associated deps.
        """
        source_router = self._source_session.network.get_router(resource_id)
        if not source_router:
            raise exception.NotFound(f"Router not found: {resource_id}")

        external_gateway_network_id: str | None = None
        external_gateway_fixed_ips: list[dict[str, str]] = []

        egi = source_router.external_gateway_info or {}
        if egi:
            src_net_id = egi.get("network_id")
            if src_net_id:
                external_gateway_network_id = (
                    self._get_associated_resource_destination_id(
                        "network", src_net_id, migrated_associated_resources
                    )
                )

            for fixed_ip in egi.get("external_fixed_ips", []) or []:
                src_subnet_id = fixed_ip.get("subnet_id")
                if not src_subnet_id:
                    continue

                dest_subnet_id = self._get_associated_resource_destination_id(
                    "subnet", src_subnet_id, migrated_associated_resources
                )

                entry: dict[str, str] = {"subnet_id": dest_subnet_id}
                ip_address = fixed_ip.get("ip_address")
                if ip_address:
                    entry["ip_address"] = ip_address

                external_gateway_fixed_ips.append(entry)

        fields = [
            "availability_zone_hints",
            "description",
            "external_gateway_info",
            "flavor_id",
            "is_admin_state_up",
            "is_distributed",
            "is_ha",
            "name",
        ]

        kwargs: dict = {}
        for field in fields:
            value = getattr(source_router, field, None)
            if value is None:
                continue

            if field == "external_gateway_info":
                if not egi:
                    continue
                new_egi = dict(egi)
                if external_gateway_network_id:
                    new_egi["network_id"] = external_gateway_network_id
                if external_gateway_fixed_ips:
                    new_egi["external_fixed_ips"] = external_gateway_fixed_ips
                kwargs[field] = new_egi
            else:
                kwargs[field] = value

        dest_router = self._destination_session.network.create_router(**kwargs)
        return dest_router.id

    # -------------------------------------------------------------------------
    # Connect internal members to the migrated router
    # -------------------------------------------------------------------------
    def connect_member_resources_to_parent(
        self,
        parent_resource_id: str,
        member_resources: list[tuple[str, str, str]],
    ):
        """Connect internal member subnets to the destination router.

        member_resources contains (resource_type, source_id, destination_id).
        """
        for resource_type, source_id, dest_id in member_resources:
            if resource_type != "subnet":
                continue

            LOG.info(
                "Attaching internal subnet %s (dest %s) to router %s",
                source_id,
                dest_id,
                parent_resource_id,
            )
            try:
                self._destination_session.network.add_interface_to_router(
                    parent_resource_id,
                    subnet_id=dest_id,
                )
            except openstack_exc.ConflictException:
                LOG.debug(
                    "Interface for router %s on subnet %s already exists",
                    parent_resource_id,
                    dest_id,
                )

    # -------------------------------------------------------------------------
    # Source listing + delete
    # -------------------------------------------------------------------------
    def get_source_resource_ids(self, resource_filters: dict[str, str]) -> list[str]:
        """Returns a list of resource ids based on the specified filters."""
        self._validate_resource_filters(resource_filters)

        query_filters: dict[str, str] = {}
        if "owner_id" in resource_filters:
            query_filters["project_id"] = resource_filters["owner_id"]

        routers = self._source_session.network.routers(**query_filters)
        return [router.id for router in routers]

    def _delete_resource(self, resource_id: str, openstack_session):
        openstack_session.network.delete_router(resource_id, ignore_missing=True)
