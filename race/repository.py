import json
from datetime import datetime, timezone
from uuid import uuid4

from race.db import json_dump
from race.errors import AppError

RESOURCE_KINDS = ("folders", "workbooks", "sheets", "charts", "data_sources")
FIELDS = (
    "id",
    "name",
    "owner_id",
    "parent_id",
    "config_json",
    "version",
    "created_at",
    "updated_at",
    "deleted_at",
    "cloned_from_id",
    "status",
    "operation_key",
    "write_token",
)


def now():
    return datetime.now(timezone.utc)


class Repository:
    def __init__(self, db, session):
        self.db, self.session = db, session

    def decode(self, row, kind):
        item = dict(row)
        item["config"] = json.loads(item.pop("config_json"))
        item["kind"] = kind
        return item

    def get(self, kind, rid, *, incomplete=False):
        rows = self.session.execute(
            f"SELECT * FROM {self.db.table(kind)} WHERE id=:id AND deleted_at IS NULL", {"id": rid}
        )
        if len(rows) > 1:
            raise AppError(503, "Duplicate metadata IDs detected. Contact the app administrator.")
        if not rows or (not incomplete and rows[0]["status"] != "ready"):
            raise AppError(404, "Item not found")
        return self.decode(rows[0], kind)

    def list(self, kind):
        return [
            self.decode(r, kind)
            for r in self.session.execute(
                f"SELECT * FROM {self.db.table(kind)} WHERE deleted_at IS NULL AND status='ready' ORDER BY created_at"
            )
        ]

    def create(self, kind, name, owner_id, parent_id=None, config=None, **extra):
        stamp = now()
        row = dict(
            id=str(uuid4()),
            name=name,
            owner_id=owner_id,
            parent_id=parent_id,
            config_json=json_dump(config or {}),
            version=1,
            created_at=stamp,
            updated_at=stamp,
            deleted_at=None,
            cloned_from_id=None,
            status="ready",
            operation_key=None,
            write_token=str(uuid4()),
        )
        row.update(extra)
        columns = ",".join(FIELDS)
        values = ",".join(f":{f}" for f in FIELDS)
        self.session.execute(f"INSERT INTO {self.db.table(kind)} ({columns}) VALUES ({values})", row)
        return self.get(kind, row["id"], incomplete=True)

    def update(self, item, expected_version, **changes):
        if item["version"] != expected_version:
            raise AppError(409, "This item changed in another session. Reload before saving.")
        if "config" in changes:
            changes["config_json"] = json_dump(changes.pop("config"))
        if not set(changes) <= set(FIELDS) - {"id", "created_at", "owner_id", "version"}:
            raise ValueError("Invalid metadata update")
        token = str(uuid4())
        changes.update(updated_at=now(), write_token=token)
        assignments = ",".join(f"{k}=:{k}" for k in changes)
        self.session.execute(
            f"UPDATE {self.db.table(item['kind'])} SET {assignments},version=version+1 "
            "WHERE id=:id AND version=:expected AND deleted_at IS NULL",
            dict(changes, id=item["id"], expected=expected_version),
        )
        rows = self.session.execute(
            f"SELECT write_token FROM {self.db.table(item['kind'])} WHERE id=:id", {"id": item["id"]}
        )
        if len(rows) != 1 or rows[0]["write_token"] != token:
            raise AppError(409, "Concurrent update detected. Reload before saving.")
        if changes.get("deleted_at"):
            return None
        return self.get(item["kind"], item["id"], incomplete=True)

    def permissions(self, kind, rid):
        return self.session.execute(
            f"SELECT * FROM {self.db.table('resource_permissions')} WHERE resource_kind=:kind AND resource_id=:id",
            {"kind": kind, "id": rid},
        )

    def audit(self, user_id, action, item):
        self.session.execute(
            f"INSERT INTO {self.db.table('audit_events')} "
            "(id,actor_id,action,resource_kind,resource_id,created_at,detail_json) "
            "VALUES (:id,:actor,:action,:kind,:resource,:at,:detail)",
            dict(
                id=str(uuid4()),
                actor=user_id,
                action=action,
                kind=item["kind"],
                resource=item["id"],
                at=now(),
                detail=json_dump({"version": item["version"]}),
            ),
        )
