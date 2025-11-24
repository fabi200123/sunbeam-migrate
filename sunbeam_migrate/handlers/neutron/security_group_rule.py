# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from sunbeam_migrate import config, constants, exception
from sunbeam_migrate.db import api as db_api
from sunbeam_migrate.db import models
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

    def get_implementation_status(self) -> str:
        """Describe the implementation status."""
        return constants.IMPL_PARTIAL

    def get_associated_resource_types(self) -> list[str]:
        """Security group rules depend on their security group."""
        return ["security-group"]

    def get_associated_resources(self, resource_id: str) -> list[tuple[str, str]]:
        """Return the security groups referenced by this rule."""
        source_rule = self._source_session.network.get_security_group_rule(resource_id)
        if not source_rule:
            raise Exception(f"Security Group Rule not found: {resource_id}")
        resources = [("security-group", source_rule.security_group_id)]
        if source_rule.remote_group_id:
            resources.append(("security-group", source_rule.remote_group_id))
        return resources

    def _get_migrated_security_group_id(self, source_sg_id: str) -> str | None:
        session = db_api.get_session()
        migration = (
            session.query(models.Migration)
            .filter_by(
                resource_type="security-group",
                source_id=source_sg_id,
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
        source_sg_rule = self._source_session.network.get_security_group_rule(
            resource_id
        )
        if not source_sg_rule:
            raise Exception(f"Security Group Rule not found: {resource_id}")

        def _resolve_sg_id(source_sg_id: str) -> str:
            try:
                return self._get_associated_resource_destination_id(
                    "security-group",
                    source_sg_id,
                    migrated_associated_resources,
                )
            except exception.NotFound:
                dest_id = self._get_migrated_security_group_id(source_sg_id)
                if dest_id:
                    return dest_id
                raise exception.InvalidInput(
                    "Unable to find migrated destination security group %s "
                    "required for rule %s. Migrate the security group first or "
                    "rerun with '--include-dependencies'."
                    % (source_sg_id, resource_id)
                )

        dest_security_group_id = _resolve_sg_id(source_sg_rule.security_group_id)

        fields = [
            "description",
            "direction",
            "port_range_max",
            "port_range_min",
            "protocol",
            "project_id",
            "remote_ip_prefix",
        ]
        kwargs = {}
        for field in fields:
            if field == "project_id":
                value = getattr(source_sg_rule, "project_id", None) or getattr(
                    source_sg_rule, "tenant_id", None
                )
            else:
                value = getattr(source_sg_rule, field, None)
            if value:
                kwargs[field] = value

        # Handle ether_type (different attribute name)
        ether_type = getattr(source_sg_rule, "ethertype", None)
        if ether_type:
            kwargs["ether_type"] = ether_type

        # Handle remote_group_id (needs resolution)
        if source_sg_rule.remote_group_id:
            kwargs["remote_group_id"] = _resolve_sg_id(source_sg_rule.remote_group_id)

        destination_sg_rule = self._destination_session.network.create_security_group_rule(
            security_group_id=dest_security_group_id,
            **kwargs,
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
