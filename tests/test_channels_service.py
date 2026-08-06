from tg_summariser.services.channels import extract_channel_usernames


def test_extract_channel_usernames_from_bulk_list() -> None:
    text = """
    @system_analyse — Ольга Пономарева 31 877
    @sys_sa — Системный Аналитик 19 039
    @lib_analyst — Библиотека СА 13 881
    https://t.me/systemswing — Системный сдвиг 10 211
    @system_analyse — duplicate
    """

    usernames = extract_channel_usernames(text)

    assert usernames == ["system_analyse", "sys_sa", "lib_analyst", "systemswing"]
