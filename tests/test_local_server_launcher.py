from scripts.start_local_server import normalized_child_environment, port_is_open


def test_normalized_child_environment_keeps_one_case_insensitive_path_key() -> None:
    env = normalized_child_environment({"Path": "one", "PATH": "two", "OTHER": "value"})
    assert env == {"PATH": "two", "OTHER": "value"}


def test_port_is_open_is_false_for_unbound_local_port() -> None:
    assert port_is_open("127.0.0.1", 58991) is False
