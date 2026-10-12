
def pointer_get(obj, path):
    cur = obj
    for tok in path.strip("/").split("/"):
        if not tok:
            continue
        tok = tok.replace("~1", "/").replace("~0", "~")
        cur = cur[int(tok)] if isinstance(cur, list) else cur[tok]
    return cur

def pointer_set(obj, path, value):
    parts = path.strip("/").split("/")
    cur = obj
    for tok in parts[:-1]:
        tok = tok.replace("~1", "/").replace("~0", "~")
        cur = cur[int(tok)] if isinstance(cur, list) else cur[tok]
    last = parts[-1].replace("~1", "/").replace("~0", "~")
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value

def eval_predicate(predicate_id, scenario, state):
    p = scenario["predicates"][predicate_id]
    got = pointer_get(state, p["path"])
    want = p["value"]
    op = p["op"]
    if op == "eq":
        return got == want
    if op == "ne":
        return got != want
    if op == "contains":
        return want in got
    if op == "subseteq":
        return set(got).issubset(set(want))
    raise ValueError(f"Unsupported predicate op: {op}")

def eval_expr(expr, scenario, state):
    if isinstance(expr, bool):
        return expr
    if "predicate" in expr:
        return eval_predicate(expr["predicate"], scenario, state)
    if "all" in expr:
        return all(eval_expr(x, scenario, state) for x in expr["all"])
    if "any" in expr:
        return any(eval_expr(x, scenario, state) for x in expr["any"])
    if "not" in expr:
        return not eval_expr(expr["not"], scenario, state)
    raise ValueError(f"Malformed expression: {expr}")

def commit_components(scenario, state, profile="balanced"):
    policy = scenario["policy_profiles"][profile]
    auth_id = scenario["action"]["authorization"]["predicate"]
    authorization = eval_predicate(auth_id, scenario, state)
    justification = eval_expr(policy["justification"], scenario, state)
    binding = eval_expr(policy["binding"], scenario, state)
    scope = eval_expr(policy["scope"], scenario, state)
    executable = eval_expr(scenario["action"]["execution_precondition"], scenario, state)
    valid = authorization and justification and binding and scope
    return {
        "authorization": authorization,
        "justification": justification,
        "binding": binding,
        "scope": scope,
        "executable": executable,
        "valid": valid,
        # A true EA-TOCTOU attack state must still allow consequential actuation.
        "attack_goal": authorization and executable and not valid,
    }
