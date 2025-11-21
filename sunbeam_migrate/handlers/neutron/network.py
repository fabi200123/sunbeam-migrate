# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from sunbeam_migrate import config, constants
from sunbeam_migrate.handlers import base

CONF = config.get_config()


class NetworkHandler(base.BaseMigrationHandler):
    """Handle Barbican secret container migrations."""

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

    def get_associated_resource_types(self) -> list[str]:
        """Get a list of associated resource types.

        Associated resources must be migrated first.
        """
        return []

    def get_member_resource_types(self) -> list[str]:
        """Get a list of member (contained) resource types.

        The migrations can cascade to contained resources.
        """
        return ["network"]

    def perform_individual_migration(
        self,
        resource_id: str,
        migrated_associated_resources: list[tuple[str, str, str]],
    ) -> str:
        """Migrate the specified resource.

        :param resource_id: the resource to be migrated
        :param migrated_associated_resources: a list of tuples describing
            associated resources that have already been migrated.
            Format: (resource_type, source_id, destination_id)

        Return the resulting resource id.
        """
        source_network = self._source_session.network.get_network(resource_id)
        if not source_network:
            raise Exception(f"Network not found: {resource_id}")

        network_attrs = {
            "availability_zone_hints": source_network.availability_zone_hints,
            "description": source_network.description,
            "dns_domain": source_network.dns_domain,
            "is_admin_state_up": source_network.is_admin_state_up,
            "is_default": source_network.is_default,
            "is_port_security_enabled": source_network.is_port_security_enabled,
            "is_router_external": source_network.is_router_external,
            "is_shared": source_network.is_shared,
            "mtu": source_network.mtu,
            "name": source_network.name,
            "project_id": source_network.project_id,
            "provider_network_type": source_network.provider_network_type,
            "provider_physical_network": source_network.provider_physical_network,
            "provider_segmentation_id": source_network.provider_segmentation_id,
            "segments": source_network.segments,
        }

        kwargs = {}
        for field in network_attrs:
            value = getattr(source_network, field, None)
            if value:
                kwargs[field] = value
        dest_network = self._destination_session.network.create_network(
            **kwargs
        )

        return dest_network.id

    def get_source_resource_ids(self, resource_filters: dict[str, str]) -> list[str]:
        """Returns a list of resource ids based on the specified filters.

        Raises an exception if any of the filters are unsupported.
        """
        self._validate_resource_filters(resource_filters)

        query_filters = {}
        if "owner_id" in resource_filters:
            # Network uses tenant_id instead of owner_id
            query_filters["tenant_id"] = resource_filters["owner_id"]

        resource_ids = []
        for resource in self._source_session.network.networks(**query_filters):
            resource_ids.append(resource.id)

        return resource_ids

    def _delete_resource(self, resource_id: str, openstack_session):
        raise NotImplementedError()
