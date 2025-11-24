# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from sunbeam_migrate import config, constants, exception
from sunbeam_migrate.db import api as db_api
from sunbeam_migrate.db import models
from sunbeam_migrate.handlers import base

CONF = config.get_config()


class SubnetHandler(base.BaseMigrationHandler):
    """Handle Barbican secret container migrations."""

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

    def get_implementation_status(self) -> str:
        """Describe the implementation status."""
        return constants.IMPL_PARTIAL

    def get_associated_resources(self, resource_id: str) -> list[tuple[str, str]]:
        """Return the source resources this subnet depends on."""
        source_subnet = self._source_session.network.get_subnet(resource_id)
        if not source_subnet:
            raise Exception(f"Subnet not found: {resource_id}")
        
        associated_resources = []
        for network_ref in [source_subnet.network_id]:
            associated_resources.append(("network", network_ref))

        return associated_resources

    def _get_migrated_network_id(self, source_network_id: str) -> str | None:
        """Return destination id for an already migrated network, if any."""
        session = db_api.get_session()
        migration = (
            session.query(models.Migration)
            .filter_by(
                resource_type="network",
                source_id=source_network_id,
                status=constants.STATUS_COMPLETED,
            )
            .order_by(models.Migration.created_at.desc())
            .first()
        )
        if migration:
            return migration.destination_id
        return None

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

        :param resource_id: the resource to be migrated
        :param migrated_associated_resources: a list of tuples describing
            associated resources that have already been migrated.
            Format: (resource_type, source_id, destination_id)

        Return the resulting resource id.
        """
        source_subnet = self._source_session.network.get_subnet(resource_id)
        if not source_subnet:
            raise Exception(f"Subnet not found: {resource_id}")

        # Resolve the destination network id (the network must have been migrated already).
        try:
            destination_network_id = self._get_associated_resource_destination_id(
                "network",
                source_subnet.network_id,
                migrated_associated_resources,
            )
        except exception.NotFound:
            destination_network_id = self._get_migrated_network_id(
                source_subnet.network_id
            )

        if not destination_network_id:
            raise exception.InvalidInput(
                "Unable to find migrated destination network for subnet "
                f"{resource_id}. Migrate the parent network first or rerun with "
                "'--include-dependencies'."
            )

        fields = [
            "allocation_pools",
            "cidr",
            "description",
            "dns_nameservers",
            "dns_publish_fixed_ip",
            "gateway_ip",
            "host_routes",
            "ip_version",
            "ipv6_address_mode",
            "ipv6_ra_mode",
            "name",
            "prefix_length",
            "project_id",
            "segment_id",
            "service_types",
            "subnet_pool_id",
        ]
        kwargs = {}
        for field in fields:
            if field == "dns_publish_fixed_ip":
                value = getattr(source_subnet, "dns_publish_fixed_ip", None)
            elif field == "host_routes":
                value = getattr(source_subnet, "host_routes", None)
            elif field == "prefix_length":
                value = getattr(source_subnet, "prefix_length", None)
            elif field == "project_id":
                value = getattr(source_subnet, "project_id", None) or getattr(
                    source_subnet, "tenant_id", None
                )
            elif field == "subnet_pool_id":
                value = getattr(
                    source_subnet,
                    "subnet_pool_id",
                    getattr(source_subnet, "subnetpool_id", None),
                )
            else:
                value = getattr(source_subnet, field, None)
            if value:
                kwargs[field] = value

        # Handle boolean fields that can be False
        is_dhcp_enabled = getattr(
            source_subnet,
            "is_dhcp_enabled",
            getattr(source_subnet, "enable_dhcp", None),
        )
        if is_dhcp_enabled is not None:
            kwargs["is_dhcp_enabled"] = is_dhcp_enabled
        if source_subnet.use_default_subnet_pool is not None:
            kwargs["use_default_subnet_pool"] = source_subnet.use_default_subnet_pool

        kwargs["network_id"] = destination_network_id

        destination_subnet = self._destination_session.network.create_subnet(**kwargs)
        return destination_subnet.id

    def get_source_resource_ids(self, resource_filters: dict[str, str]) -> list[str]:
        """Returns a list of resource ids based on the specified filters.

        Raises an exception if any of the filters are unsupported.
        """
        self._validate_resource_filters(resource_filters)

        query_filters = {}
        if "owner_id" in resource_filters:
            query_filters["project_id"] = resource_filters["owner_id"]

        resource_ids = []
        for resource in self._source_session.network.subnets(**query_filters):
            resource_ids.append(resource.id)

        return resource_ids

    def _delete_resource(self, resource_id: str, openstack_session):
        raise NotImplementedError()
