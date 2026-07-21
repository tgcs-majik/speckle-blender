"""End-to-end publish test — runs INSIDE headless Blender against a real server.

    blender --background --python tests/e2e/test_publish_e2e.py

Creates a throwaway model, publishes two real cube meshes through the connector's
``publish_operation``, asserts a version is created on the server, then deletes the
throwaway model. Exits 0 on success, 1 on failure (so it is CI-usable). Requires:

    SPECKLE_TOKEN, SPECKLE_ACCOUNT_ID, SPECKLE_PROJECT   (+ optional SPECKLE_SERVER)

This is the one layer that cannot run outside Blender: it exercises the real
bpy geometry -> Speckle conversion -> streamed upload -> version.create path.
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

        from bl_ext.user_default.speckle_blender_addon.connector.operations.publish_operation import (  # noqa: E501
            publish_operation,
        )

        wm = bpy.context.window_manager
        wm.selected_account_id = ACCOUNT_ID
        wm.selected_project_id = PROJECT
        wm.selected_model_id = model_id

        ok, msg, version_id = publish_operation(bpy.context, [c1, c2], "e2e test", True)
        if not ok:
            _fail(f"publish_operation not ok: {msg}")
        if not version_id:
            _fail("no version id returned")
        print(f"[e2e] publish ok, version {version_id}")

        server_state = gql(
            "query($p:String!,$m:String!){project(id:$p){model(id:$m){"
            "versions(limit:1){totalCount items{id sourceApplication}}}}}",
            {"p": PROJECT, "m": model_id},
        )
        versions = server_state["data"]["project"]["model"]["versions"]
        if versions["totalCount"] != 1:
            _fail(f"expected 1 version on server, got {versions['totalCount']}")
        if versions["items"][0]["id"] != version_id:
            _fail("server version id does not match returned id")
        if versions["items"][0]["sourceApplication"] != "blender":
            _fail("sourceApplication is not 'blender'")

        print(f"E2E_PASS version={version_id}")
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
