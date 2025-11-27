# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from sunbeam_migrate import config, constants, exception
from sunbeam_migrate.handlers import base

CONF = config.get_config()

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

    def get_associated_resource_types(self) -> list[str]:
        """Get a list of associated resource types.

        Associated resources must be migrated first.
        """
        return ["network"]

    def get_associated_resources(self, resource_id):
        """Return the source resources this router depends on."""
        source_router = self._source_session.network.get_router(resource_id)
        if not source_router:
            raise exception.NotFound(f"Router not found: {resource_id}")

        associated_resources = []
        for network_ref in [source_router.external_gateway_info.get("network_id")]:
            if network_ref:
                associated_resources.append(("network", network_ref))

        return associated_resources

    def get_implementation_status(self) -> str:
        """Describe the implementation status."""
        return constants.IMPL_PARTIAL

    def get_member_resource_types(self) -> list[str]:
        """Get a list of member (contained) resource types.

        The migrations can cascade to contained resources.
        """
        return []

    def perform_individual_migration(
        self,
        resource_id: str,
        migrated_associated_resources: list[tuple[str, str, str]],
    ) -> str:
        """Migrate the specified resource.

        :param resource_id: The ID of the resource to migrate.
        :param migrated_associated_resources: A list of tuples containing the
            resource type, source ID, and destination ID of associated resources
            that have already been migrated.
        
        Return the resulting resource id.
        """
        source_router = self._source_session.network.get_router(resource_id)
        if not source_router:
            raise exception.NotFound(f"Router not found: {resource_id}")

        external_gateway_network_id = None
        if source_router.external_gateway_info:
            external_gateway_network_id = self._get_associated_resource_destination_id(
                "network",
                source_router.external_gateway_info.get("network_id"),
                migrated_associated_resources,
            )
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

        kwargs = {}
        for field in fields:
            value = getattr(source_router, field, None)
            if value is not None:
                kwargs[field] = value
                if field == "external_gateway_info" and external_gateway_network_id:
                    kwargs[field]["network_id"] = external_gateway_network_id
        
        destination_router = self._destination_session.network.create_router(**kwargs)
        return destination_router.id

    def get_source_resource_ids(self, resource_filters):
        """Returns a list of resource ids based on the specified filters.

        Raises an exception if any of the filters are unsupported.
        """
        self._validate_resource_filters(resource_filters)

        query_filters = {}
        if "owner_id" in resource_filters:
            query_filters["project_id"] = resource_filters["owner_id"]

        resource_ids = []
        for resource in self._source_session.network.routers(**query_filters):
            resource_ids.append(resource.id)

        return resource_ids

    def _delete_resource(self, resource_id: str, openstack_session):
        openstack_session.network.delete_router(resource_id, ignore_missing=True)
