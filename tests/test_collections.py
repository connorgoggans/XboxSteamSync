import json

from xboxsteamsync import steam


def _collection(entries, name="Xbox"):
    for key, e in entries:
        if key.startswith("user-collections.") and "value" in e:
            v = json.loads(e["value"])
            if v["name"] == name:
                return e, v
    return None, None


def _existing(added, removed=(), version="7"):
    value = {"id": "uc-abc", "name": "Xbox", "added": list(added), "removed": list(removed)}
    entry = {"key": "user-collections.uc-abc", "timestamp": 1, "value": json.dumps(value), "version": version}
    other = {"key": "user-collections.uc-other", "timestamp": 1,
             "value": json.dumps({"id": "uc-other", "name": "Other", "added": [5]}), "version": "9"}
    return [[entry["key"], entry], [other["key"], other]], [[1, "9"]]


def test_creates_collection():
    entries, modified = [], []
    changed, namespaces = steam.update_collection(entries, [], modified, "Xbox", [10, 20], [])
    assert changed
    entry, value = _collection(entries)
    assert value["added"] == [10, 20] and value["id"].startswith("uc-")
    assert entry["version"] == "1" and namespaces == [[1, "1"]]
    assert modified == [entry["key"]]


def test_does_not_create_empty_collection():
    entries = []
    assert steam.update_collection(entries, [], [], "Xbox", [], [])[0] is False
    assert entries == []


def test_add_remove_keeps_hand_added_and_bumps_version():
    entries, namespaces = _existing(added=[1, 99, 2])  # 99 was added by hand
    modified = []
    changed, namespaces = steam.update_collection(entries, namespaces, modified, "Xbox", [1, 3], [2])
    assert changed
    entry, value = _collection(entries)
    assert value["added"] == [1, 99, 3]
    assert entry["version"] == "10"  # above both the namespace (9) and every entry
    assert namespaces == [[1, "10"]]
    assert modified == ["user-collections.uc-abc"]
    assert _collection(entries, "Other")[1]["added"] == [5]


def test_member_is_taken_out_of_removed_list():
    entries, namespaces = _existing(added=[1], removed=[2, 8])
    changed, _ = steam.update_collection(entries, namespaces, [], "Xbox", [1, 2], [])
    assert changed
    assert _collection(entries)[1]["removed"] == [8]


def test_no_change_leaves_everything_alone():
    entries, namespaces = _existing(added=[1, 2])
    before = json.dumps(entries)
    changed, ns = steam.update_collection(entries, namespaces, [], "Xbox", [1, 2], [])
    assert not changed and ns == namespaces and json.dumps(entries) == before


def test_delete_if_empty():
    entries, namespaces = _existing(added=[1, 2])
    changed, _ = steam.update_collection(entries, namespaces, [], "Xbox", [], [1, 2], delete_if_empty=True)
    assert changed
    entry = dict(entries)["user-collections.uc-abc"]
    assert entry["is_deleted"] is True and "value" not in entry
    assert _collection(entries) == (None, None)


def test_delete_if_empty_keeps_collection_with_hand_added_games():
    entries, namespaces = _existing(added=[1, 99])
    steam.update_collection(entries, namespaces, [], "Xbox", [], [1], delete_if_empty=True)
    assert _collection(entries)[1]["added"] == [99]


def test_sync_collection_on_disk(tmp_path):
    cloud = tmp_path / "cloudstorage"
    cloud.mkdir()
    entries, namespaces = _existing(added=[1])
    (cloud / "cloud-storage-namespace-1.json").write_text(json.dumps(entries))
    (cloud / "cloud-storage-namespaces.json").write_text(json.dumps(namespaces))
    assert steam.sync_collection(tmp_path, "Xbox", [1, 2], [], write=False)
    assert _collection(json.loads((cloud / "cloud-storage-namespace-1.json").read_text()))[1]["added"] == [1]
    assert steam.sync_collection(tmp_path, "Xbox", [1, 2], [])
    assert _collection(json.loads((cloud / "cloud-storage-namespace-1.json").read_text()))[1]["added"] == [1, 2]
    assert json.loads((cloud / "cloud-storage-namespace-1.modified.json").read_text()) == ["user-collections.uc-abc"]
    assert not steam.sync_collection(tmp_path, "Xbox", [1, 2], [])


def test_missing_collections_file(tmp_path):
    assert steam.sync_collection(tmp_path, "Xbox", [1], []) is False
