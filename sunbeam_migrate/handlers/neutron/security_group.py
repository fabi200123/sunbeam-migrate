# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from sunbeam_migrate import config
from sunbeam_migrate.handlers import base

CONF = config.get_config()


class SecurityGroupHandler(base.BaseMigrationHandler):
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
        return []

    def get_member_resource_types(self) -> list[str]:
        """Get a list of member (contained) resource types.

        The migrations can cascade to contained resources.
        """
        return ["security-group-rule"]

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
        source_sg = self._source_session.network.get_security_group(resource_id)
        if not source_sg:
            raise Exception(f"Security Group not found: {resource_id}")

        sec_group_attrs = {
            "description": source_sg.description,
            "name": source_sg.name,
            "stateful": source_sg.is_stateful,
            "project_id": source_sg.project_id,
        }

        kwargs = {}
        for field in sec_group_attrs:
            value = getattr(source_sg, field, None)
            if value:
                kwargs[field] = value

        dest_sg = self._destination_session.network.create_security_group(
            **kwargs
        )
        return dest_sg.id

    def get_source_resource_ids(self, resource_filters: dict[str, str]) -> list[str]:
        """Returns a list of resource ids based on the specified filters.

        Raises an exception if any of the filters are unsupported.
        """
        self._validate_resource_filters(resource_filters)

        query_filters = {}
        if "owner_id" in resource_filters:
            query_filters["tenant_id"] = resource_filters["owner_id"]

        source_security_groups = self._source_session.network.list_security_groups(
            **query_filters
        )
        return [sg.id for sg in source_security_groups]

    def _delete_resource(self, resource_id: str, openstack_session):
        raise NotImplementedError()
