"""End-to-end publish tests — run INSIDE headless Blender against a real server.

    blender --background --python tests/e2e/test_publish_e2e.py

Two phases, both against a self-cleaning throwaway model:

1. ``publish_operation`` (the synchronous path used by the simple publish op).
2. ``send_and_create_version`` — the bpy-free worker the modal publish operator
   runs on its background thread — driven with a real ``build_collection_hierarchy``
   conversion + ``ProgressServerTransport``.

The modal operator's timer/thread orchestration itself is the only piece that
cannot run head-less (Blender's modal loop needs a real event loop); every unit
of work it performs is covered here. Exits 0 on success, 1 on any failure.
Requires: SPECKLE_TOKEN, SPECKLE_ACCOUNT_ID, SPECKLE_PROJECT (+ SPECKLE_SERVER).
"""

import json
import os
import ssl
import sys
import traceback
import urllib.request

TOKEN = os.environ["SPECKLE_TOKEN"]
ACCOUNT_ID = os.environ["SPECKLE_ACCOUNT_ID"]
PROJECT = os.environ["SPECKLE_PROJECT"]
SERVER = os.environ.get("SPECKLE_SERVER", "https://speckle.tgcs.com.au")
_CTX = ssl.create_default_context(
    cafile=os.environ.get("SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt")
)

_ADDON = "bl_ext.user_default.speckle_blender_addon"


def gql(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        SERVER + "/graphql",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + TOKEN,
        },
    )
    return json.loads(urllib.request.urlopen(req, timeout=30, context=_CTX).read())


def _fail(msg):
    print(f"E2E_FAIL: {msg}")
    sys.exit(1)


def _version_count(model_id):
    res = gql(
        "query($p:String!,$m:String!){project(id:$p){model(id:$m){"
        "versions{totalCount items{id sourceApplication}}}}}",
        {"p": PROJECT, "m": model_id},
    )
    return res["data"]["project"]["model"]["versions"]


def _phase_publish_operation(model_id, cubes):
    import bpy

    from importlib import import_module

    publish_operation = import_module(
        f"{_ADDON}.connector.operations.publish_operation"
    ).publish_operation

    wm = bpy.context.window_manager
    wm.selected_account_id = ACCOUNT_ID
    wm.selected_project_id = PROJECT
    wm.selected_model_id = model_id

    ok, msg, version_id = publish_operation(bpy.context, cubes, "e2e sync", True)
    if not ok or not version_id:
        _fail(f"publish_operation failed: {msg}")
    print(f"[e2e] phase 1 (publish_operation) ok, version {version_id}")
    return version_id


def _phase_worker(model_id, cubes):
    """Exercise exactly what the modal operator's background thread runs."""
    import bpy

    from importlib import import_module

    po = import_module(f"{_ADDON}.connector.operations.publish_operation")
    pt = import_module(f"{_ADDON}.connector.operations.progress_transport")
    am = import_module(f"{_ADDON}.connector.utils.account_manager")

    root = po.build_collection_hierarchy(bpy.context, cubes, True)
    if not root:
        _fail("build_collection_hierarchy returned nothing")
    po.add_render_material_proxies_to_base(root, cubes)
    total = po.count_objects_in_collection(root)

    client = am._client_cache.get_client(ACCOUNT_ID)
    transport = pt.ProgressServerTransport(
        stream_id=PROJECT, client=client, wm=None, total=total
    )
    version_id = po.send_and_create_version(
        client, root, PROJECT, model_id, "e2e worker", transport, po._build_source_data()
    )
    if not version_id:
        _fail("send_and_create_version returned no version id")
    if transport.progress_count <= 0:
        _fail("transport reported no progress")
    print(
        f"[e2e] phase 2 (worker) ok, version {version_id}, "
        f"objects streamed {transport.progress_count}"
    )
    return version_id


def main():
    import bpy

    created = gql(
        "mutation($i:CreateModelInput!){modelMutations{create(input:$i){id}}}",
        {"i": {"projectId": PROJECT, "name": "zz-e2e-publish-test"}},
    )
    model_id = created["data"]["modelMutations"]["create"]["id"]
    print(f"[e2e] throwaway model: {model_id}")

    try:
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        c1 = bpy.context.active_object
        bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
        c2 = bpy.context.active_object
        cubes = [c1, c2]

        v1 = _phase_publish_operation(model_id, cubes)
        v2 = _phase_worker(model_id, cubes)

        versions = _version_count(model_id)
        if versions["totalCount"] != 2:
            _fail(f"expected 2 versions on server, got {versions['totalCount']}")
        ids = {v["id"] for v in versions["items"]}
        if {v1, v2} - ids:
            _fail("a returned version id is missing from the server")
        if any(v["sourceApplication"] != "blender" for v in versions["items"]):
            _fail("a version has the wrong sourceApplication")

        print(f"E2E_PASS versions={v1},{v2}")
    finally:
        gql(
            "mutation($i:DeleteModelInput!){modelMutations{delete(input:$i)}}",
            {"i": {"id": model_id, "projectId": PROJECT}},
        )
        print(f"[e2e] cleaned up model {model_id}")


if __name__ == "__main__":
    try:
        main()
        sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        _fail("unhandled exception")
