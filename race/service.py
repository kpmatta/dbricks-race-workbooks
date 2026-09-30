from contextlib import contextmanager
from copy import deepcopy
from threading import RLock
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import ValidationError

from race.authorization import ancestors, require, role_for
from race.errors import AppError
from race.models import ChartConfig, SheetConfig
from race.query import Compiler, QueryService
from race.repository import Repository, now


class WorkspaceService:
    def __init__(self, db):
        self.db = db
        self.lock = RLock()
        self.queries = QueryService(db)

    @contextmanager
    def repository(self, write=False):
        # The Delta adapter supports one application writer. CAS additionally detects stale clients.
        with self.lock, self.db.session(transaction=write) as session:
            yield Repository(self.db, session)

    @staticmethod
    def public(item, role=None):
        result = {
            k: v for k, v in item.items() if k not in ("write_token", "operation_key", "deleted_at", "status")
        }
        if role:
            result["role"] = role
        return result

    def tree(self, user):
        items = []
        with self.repository() as repo:
            for kind in ("folders", "workbooks", "sheets", "charts"):
                for item in repo.list(kind):
                    try:
                        role = role_for(repo, item, user)
                        if role:
                            items.append(self.public(item, role))
                    except AppError as exc:
                        if exc.status != 404:
                            raise
        return items

    def get(self, kind, rid, user):
        with self.repository() as repo:
            item = repo.get(kind, rid)
            return self.public(item, require(repo, item, user))

    def unique_name(self, repo, kind, name, parent_id, owner_id, exclude=None):
        # Sheets and charts share the workbook's tab namespace.
        kinds = ("sheets", "charts") if kind in ("sheets", "charts") else (kind,)
        for check_kind in kinds:
            for item in repo.list(check_kind):
                if (
                    item["id"] != exclude
                    and item["parent_id"] == parent_id
                    and (parent_id is not None or item["owner_id"] == owner_id)
                    and item["name"].casefold() == name.casefold()
                ):
                    raise AppError(409, "An item with this name already exists here")

    def parent(self, repo, kind, parent_id, user):
        if kind in ("sheets", "charts"):
            if not parent_id:
                raise AppError(422, "Sheets and charts need a workbook")
            parent = repo.get("workbooks", parent_id)
        elif parent_id:
            parent = repo.get("folders", parent_id)
        else:
            return None
        require(repo, parent, user, "editor")
        if len(ancestors(repo, parent)) >= 30:
            raise AppError(422, "Maximum folder depth reached")
        return parent

    def validate_config(self, repo, kind, config, parent_id, user):
        try:
            if kind == "sheets":
                cfg = SheetConfig.model_validate(config)
                source = repo.get("data_sources", cfg.source_id)
                self.queries.validate(source, user, cfg)
                return cfg.model_dump()
            if kind == "charts":
                cfg = ChartConfig.model_validate(config)
                sheet = repo.get("sheets", cfg.sheet_id)
                require(repo, sheet, user)
                if sheet["parent_id"] != parent_id:
                    raise AppError(422, "A chart must reference a sheet in the same workbook")
                sheet_cfg = SheetConfig.model_validate(sheet["config"])
                source = repo.get("data_sources", sheet_cfg.source_id)
                cols = self.queries.validate(source, user, sheet_cfg)
                compiler = Compiler([c["name"] for c in cols] + [c.name for c in sheet_cfg.calculations])
                compiler.column(cfg.dimension)
                if cfg.measure:
                    compiler.column(cfg.measure)
                compiler.filters(cfg.filters)
                return cfg.model_dump()
            if config:
                raise AppError(422, "Folder and workbook configuration must be empty")
            return {}
        except ValidationError as exc:
            raise AppError(422, "; ".join(e["msg"] for e in exc.errors())) from None

    def create(self, kind, body, user):
        with self.repository(write=True) as repo:
            parent = self.parent(repo, kind, body.parent_id, user)
            owner = parent["owner_id"] if parent else user.id
            self.unique_name(repo, kind, body.name, body.parent_id, owner)
            config = self.validate_config(repo, kind, body.config, body.parent_id, user)
            item = repo.create(kind, body.name, owner, body.parent_id, config)
            repo.audit(user.id, "create", item)
            return self.public(item, require(repo, item, user))

    def update(self, kind, rid, body, user):
        with self.repository(write=True) as repo:
            item = repo.get(kind, rid)
            role = require(repo, item, user, "editor")
            changes = body.model_dump(exclude_unset=True, exclude={"version"})
            if changes.get("name", "") is None or ("config" in changes and changes["config"] is None):
                raise AppError(422, "Name and configuration cannot be null")
            if "parent_id" in changes and changes["parent_id"] != item["parent_id"]:
                require(repo, item, user, "owner")
                if kind in ("sheets", "charts"):
                    raise AppError(422, "Clone the workbook to move its sheets and charts together")
                parent = self.parent(repo, kind, changes["parent_id"], user)
                if parent and parent["owner_id"] != item["owner_id"]:
                    raise AppError(422, "Move within the same owner's folders; use Clone to change ownership")
                if parent and any(p["id"] == rid for p in ancestors(repo, parent)):
                    raise AppError(422, "A folder cannot be moved into itself or its descendants")
            parent_id = changes.get("parent_id", item["parent_id"])
            self.unique_name(repo, kind, changes.get("name", item["name"]), parent_id, item["owner_id"], rid)
            if "config" in changes:
                changes["config"] = self.validate_config(repo, kind, changes["config"], parent_id, user)
            updated = repo.update(item, body.version, **changes)
            repo.audit(user.id, "update", updated)
            return self.public(updated, role)

    def delete(self, kind, rid, version, user):
        with self.repository(write=True) as repo:
            item = repo.get(kind, rid)
            require(repo, item, user, "owner" if kind in ("folders", "workbooks") else "editor")
            if kind == "sheets":
                for chart in repo.list("charts"):
                    if chart["parent_id"] == item["parent_id"] and chart["config"]["sheet_id"] == rid:
                        raise AppError(409, "Delete charts referencing this sheet before deleting it")
            repo.update(item, version, deleted_at=now())
            repo.audit(user.id, "delete", item)
            # Descendants remain as tombstoned history and become inaccessible through ancestor checks.

    def permissions(self, kind, rid, user):
        if kind not in ("folders", "workbooks"):
            raise AppError(422, "Share the containing folder or workbook")
        with self.repository() as repo:
            item = repo.get(kind, rid)
            require(repo, item, user, "owner")
            return {
                "version": item["version"],
                "direct": repo.permissions(kind, rid),
                "inherited": [
                    p for a in ancestors(repo, item)[1:] for p in repo.permissions(a["kind"], a["id"])
                ],
            }

    def share(self, kind, rid, user, principal_id, role, version):
        if kind not in ("folders", "workbooks"):
            raise AppError(422, "Share the containing folder or workbook")
        with self.repository(write=True) as repo:
            item = repo.get(kind, rid)
            require(repo, item, user, "owner")
            if principal_id == item["owner_id"]:
                raise AppError(422, "The owner already has full access")
            updated = repo.update(item, version)
            parameters = {"kind": kind, "id": rid, "principal": principal_id}
            table = self.db.table("resource_permissions")
            if role is None:
                repo.session.execute(
                    f"DELETE FROM {table} WHERE resource_kind=:kind AND resource_id=:id "
                    "AND principal_id=:principal",
                    parameters,
                )
            else:
                existing = [p for p in repo.permissions(kind, rid) if p["principal_id"] == principal_id]
                if existing:
                    repo.session.execute(
                        f"UPDATE {table} SET role=:role,updated_at=:at "
                        "WHERE resource_kind=:kind AND resource_id=:id AND principal_id=:principal",
                        dict(parameters, role=role, at=now()),
                    )
                else:
                    repo.session.execute(
                        f"INSERT INTO {table} "
                        "(id,resource_kind,resource_id,principal_id,role,created_at,updated_at) "
                        "VALUES (:grant_id,:kind,:id,:principal,:role,:at,:at)",
                        dict(parameters, grant_id=str(uuid4()), role=role, at=now()),
                    )
            repo.audit(user.id, "share" if role else "unshare", updated)
            return self.public(updated, "owner")

    def clone(self, rid, body, user):
        clone_id = str(uuid5(NAMESPACE_URL, f"race:clone:{user.id}:{body.idempotency_key}"))
        with self.repository(write=True) as repo:
            # Reauthorize the original and destination even for retries after publication.
            original = repo.get("workbooks", rid)
            require(repo, original, user)
            self.parent(repo, "workbooks", body.folder_id, user)
            try:
                clone = repo.get("workbooks", clone_id, incomplete=True)
            except AppError as exc:
                if exc.status != 404:
                    raise
                clone = None
            if clone:
                if (
                    clone["cloned_from_id"] != rid
                    or clone["parent_id"] != body.folder_id
                    or clone["name"] != body.name
                ):
                    raise AppError(409, "This idempotency key was used for a different clone request")
                if clone["status"] == "ready":
                    return self.public(clone, require(repo, clone, user))
            else:
                self.unique_name(repo, "workbooks", body.name, body.folder_id, user.id)
                snapshot = {
                    kind: [deepcopy(i) for i in repo.list(kind) if i["parent_id"] == rid]
                    for kind in ("sheets", "charts")
                }
                clone = repo.create(
                    "workbooks",
                    body.name,
                    user.id,
                    body.folder_id,
                    {"clone_snapshot": snapshot},
                    id=clone_id,
                    status="creating",
                    cloned_from_id=rid,
                    operation_key=body.idempotency_key,
                )
            snapshot = clone["config"]["clone_snapshot"]
            mapping = {
                item["id"]: str(uuid5(NAMESPACE_URL, f"{clone_id}:{item['id']}"))
                for group in snapshot.values()
                for item in group
            }
            for kind in ("sheets", "charts"):
                for item in snapshot[kind]:
                    cid = mapping[item["id"]]
                    try:
                        repo.get(kind, cid)
                        continue
                    except AppError as exc:
                        if exc.status != 404:
                            raise
                    config = deepcopy(item["config"])
                    if kind == "charts":
                        config["sheet_id"] = mapping[config["sheet_id"]]
                    repo.create(
                        kind, item["name"], user.id, clone_id, config, id=cid, cloned_from_id=item["id"]
                    )
            # Publish once all children exist. Dataset access is rechecked at query time.
            clone = repo.update(clone, clone["version"], status="ready", config={})
            repo.audit(user.id, "clone", clone)
            return self.public(clone, "owner")

    def sources(self, user):
        with self.repository() as repo:
            sources = repo.list("data_sources")
        visible = []
        for source in sources:
            try:
                schema = self.queries.source_schema(source, user)
                visible.append(
                    {
                        "id": source["id"],
                        "name": source["name"],
                        "table": source["config"]["table"],
                        "columns": schema,
                    }
                )
            except AppError as exc:
                if exc.status != 403:
                    raise
        return visible

    def register_source(self, body, user):
        candidate = {"config": {"table": body.table}}
        self.queries.source_schema(candidate, user)
        with self.repository(write=True) as repo:
            for source in repo.list("data_sources"):
                if source["config"]["table"].casefold() == body.table.casefold():
                    return {"id": source["id"]}
            source = repo.create("data_sources", body.name, user.id, config=candidate["config"])
            repo.audit(user.id, "register_source", source)
            return {"id": source["id"]}

    def sheet_context(self, repo, rid, user):
        sheet = repo.get("sheets", rid)
        require(repo, sheet, user)
        config = SheetConfig.model_validate(sheet["config"])
        return config, repo.get("data_sources", config.source_id)

    def query_sheet(self, rid, body, user):
        with self.repository() as repo:
            config, source = self.sheet_context(repo, rid, user)
        return self.queries.rows(source, user, config, body)

    def query_chart(self, rid, user):
        with self.repository() as repo:
            chart = repo.get("charts", rid)
            require(repo, chart, user)
            cfg = ChartConfig.model_validate(chart["config"])
            sheet = repo.get("sheets", cfg.sheet_id)
            if sheet["parent_id"] != chart["parent_id"]:
                raise AppError(409, "Chart and sheet must belong to the same workbook")
            sheet_cfg, source = self.sheet_context(repo, cfg.sheet_id, user)
        return self.queries.chart(source, user, sheet_cfg, cfg)
