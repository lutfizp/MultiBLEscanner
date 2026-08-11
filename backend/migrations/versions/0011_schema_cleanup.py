"""Remove disconnected schema fields and link manual correlation reviews.

Revision ID: 0011
Revises: 0010
"""

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def column_names(table_name: str) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def upgrade() -> None:
    if "correlation_id" not in column_names("manual_device_correlation_decisions"):
        with op.batch_alter_table("manual_device_correlation_decisions") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "correlation_id",
                    sa.String(length=36),
                    sa.ForeignKey(
                        "device_identity_correlations.id",
                        name="fk_manual_decision_correlation",
                    ),
                )
            )

    config_columns = column_names("scanner_configurations")
    removable_config_columns = [
        name
        for name in ("presence_missing_seconds", "presence_offline_seconds", "extra")
        if name in config_columns
    ]
    if removable_config_columns:
        with op.batch_alter_table("scanner_configurations") as batch_op:
            for name in removable_config_columns:
                batch_op.drop_column(name)

    identity_columns = column_names("observed_identities")
    if "fingerprint" in identity_columns:
        indexes = {
            index["name"]
            for index in sa.inspect(op.get_bind()).get_indexes("observed_identities")
        }
        if "ix_observed_identity_fingerprint" in indexes:
            op.drop_index(
                "ix_observed_identity_fingerprint",
                table_name="observed_identities",
            )
        with op.batch_alter_table("observed_identities") as batch_op:
            batch_op.drop_column("fingerprint")

    if "identity_signature" in column_names("logical_devices"):
        with op.batch_alter_table("logical_devices") as batch_op:
            batch_op.drop_column("identity_signature")

    scanner_columns = column_names("scanners")
    if "location_id" in scanner_columns:
        op.execute(
            sa.text(
                "UPDATE scanners SET "
                "building = COALESCE(building, (SELECT building FROM monitored_locations WHERE id = scanners.location_id)), "
                "floor = COALESCE(floor, (SELECT floor FROM monitored_locations WHERE id = scanners.location_id)), "
                "room = COALESCE(room, (SELECT room FROM monitored_locations WHERE id = scanners.location_id)), "
                "zone = COALESCE(zone, (SELECT zone FROM monitored_locations WHERE id = scanners.location_id)), "
                "latitude = COALESCE(latitude, (SELECT latitude FROM monitored_locations WHERE id = scanners.location_id)), "
                "longitude = COALESCE(longitude, (SELECT longitude FROM monitored_locations WHERE id = scanners.location_id)), "
                "indoor_x = COALESCE(indoor_x, (SELECT indoor_x FROM monitored_locations WHERE id = scanners.location_id)), "
                "indoor_y = COALESCE(indoor_y, (SELECT indoor_y FROM monitored_locations WHERE id = scanners.location_id))"
            )
        )
        op.execute(
            sa.text(
                "UPDATE scanners SET location_source = 'configured' "
                "WHERE location_source IS NULL AND latitude IS NOT NULL AND longitude IS NOT NULL"
            )
        )
        with op.batch_alter_table("scanners") as batch_op:
            batch_op.drop_column("location_id")

    if "monitored_locations" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("monitored_locations")


def downgrade() -> None:
    if "monitored_locations" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "monitored_locations",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("building", sa.String(length=120)),
            sa.Column("floor", sa.String(length=80)),
            sa.Column("room", sa.String(length=120)),
            sa.Column("zone", sa.String(length=120)),
            sa.Column("latitude", sa.Float()),
            sa.Column("longitude", sa.Float()),
            sa.Column("indoor_x", sa.Float()),
            sa.Column("indoor_y", sa.Float()),
            sa.Column("notes", sa.Text()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
        )
    if "location_id" not in column_names("scanners"):
        with op.batch_alter_table("scanners") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "location_id",
                    sa.String(length=36),
                    sa.ForeignKey(
                        "monitored_locations.id",
                        name="fk_scanners_location_id",
                    ),
                )
            )

    config_columns = column_names("scanner_configurations")
    with op.batch_alter_table("scanner_configurations") as batch_op:
        if "presence_missing_seconds" not in config_columns:
            batch_op.add_column(
                sa.Column(
                    "presence_missing_seconds",
                    sa.Integer(),
                    server_default="45",
                    nullable=False,
                )
            )
        if "presence_offline_seconds" not in config_columns:
            batch_op.add_column(
                sa.Column(
                    "presence_offline_seconds",
                    sa.Integer(),
                    server_default="180",
                    nullable=False,
                )
            )
        if "extra" not in config_columns:
            batch_op.add_column(
                sa.Column("extra", sa.JSON(), server_default="{}", nullable=False)
            )

    if "fingerprint" not in column_names("observed_identities"):
        with op.batch_alter_table("observed_identities") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "fingerprint",
                    sa.String(length=128),
                    server_default="",
                    nullable=False,
                )
            )
        op.create_index(
            "ix_observed_identity_fingerprint",
            "observed_identities",
            ["fingerprint"],
        )

    if "identity_signature" not in column_names("logical_devices"):
        with op.batch_alter_table("logical_devices") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "identity_signature",
                    sa.JSON(),
                    server_default="{}",
                    nullable=False,
                )
            )

    if "correlation_id" in column_names("manual_device_correlation_decisions"):
        with op.batch_alter_table("manual_device_correlation_decisions") as batch_op:
            batch_op.drop_column("correlation_id")
