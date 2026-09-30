from race.errors import AppError

LEVELS = {"viewer": 1, "editor": 2, "owner": 3}


def ancestors(repo, item):
    """Load the complete chain first: an orphan/hidden ancestor invalidates all access."""
    chain, seen = [item], {item["id"]}
    while item["parent_id"]:
        kind = "workbooks" if item["kind"] in ("sheets", "charts") else "folders"
        item = repo.get(kind, item["parent_id"])
        if item["id"] in seen or len(chain) >= 32:
            raise AppError(409, "Invalid or excessively deep folder hierarchy")
        seen.add(item["id"])
        chain.append(item)
    return chain


def role_for(repo, item, user):
    chain = ancestors(repo, item)
    role = None
    for ancestor in chain:
        if ancestor["owner_id"] == user.id:
            return "owner"
        for grant in repo.permissions(ancestor["kind"], ancestor["id"]):
            if grant["principal_id"] == user.id and LEVELS[grant["role"]] > LEVELS.get(role, 0):
                role = grant["role"]
    return role


def require(repo, item, user, minimum="viewer"):
    role = role_for(repo, item, user)
    if role is None:
        raise AppError(404, "Item not found")
    if LEVELS[role] < LEVELS[minimum]:
        raise AppError(403, f"This action requires {minimum} access")
    return role
