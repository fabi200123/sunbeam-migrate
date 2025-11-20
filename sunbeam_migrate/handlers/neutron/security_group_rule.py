# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from sunbeam_migrate import config
from sunbeam_migrate.handlers import base

CONF = config.get_config()


class SecurityGroupRuleHandler(base.BaseMigrationHandler):
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
        return ["security-group"]

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
        source_sg_rule = self._source_session.network.get_security_group_rule(resource_id)
        if not source_sg_rule:
            raise Exception(f"Security Group Rule not found: {resource_id}")

        sec_group_rule_attrs = {
            "description": source_sg_rule.description,
            "direction": source_sg_rule.direction,
            "ether_type": source_sg_rule.ethertype,
            "port_range_max": source_sg_rule.port_range_max,
            "port_range_min": source_sg_rule.port_range_min,
            "protocol": source_sg_rule.protocol,
            "remote_group_id": source_sg_rule.remote_group_id,
            "remote_ip_prefix": source_sg_rule.remote_ip_prefix,
        }

        destination_sg_rule = self._destination_session.network.create_security_group_rule(
            security_group_id=self._get_migrated_associated_resource_id(
                "security-group",
                source_sg_rule.security_group_id,
                migrated_associated_resources,
            ),
            **sec_group_rule_attrs,
        )

        return destination_sg_rule.id

    def get_source_resource_ids(self, resource_filters: dict[str, str]) -> list[str]:
        """Returns a list of resource ids based on the specified filters.

        Raises an exception if any of the filters are unsupported.
        """
        self._validate_resource_filters(resource_filters)

        query_filters = {}
        if "owner_id" in resource_filters:
            # Security Group Rule uses tenant_id instead of owner_id
            query_filters["tenant_id"] = resource_filters["owner_id"]

        resource_ids = []
        for sg_rule in self._source_session.network.security_group_rules(**query_filters):
            resource_ids.append(sg_rule.id)

        return resource_ids

    def _delete_resource(self, resource_id: str, openstack_session):
        raise NotImplementedError()
