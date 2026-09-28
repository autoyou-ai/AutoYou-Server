from tests.support.paths import find_repo_root


def test_server_only_checkout_does_not_need_private_agent_guidance(tmp_path):
    (tmp_path / "server.py").touch()
    (tmp_path / "README.md").touch()
    nested = tmp_path / "tests" / "shared"
    nested.mkdir(parents=True)
    assert find_repo_root(nested) == tmp_path
