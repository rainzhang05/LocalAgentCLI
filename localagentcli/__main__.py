"""Entry point for the localagent CLI."""


def main():
    """Launch the LocalAgent interactive shell."""
    from localagentcli.config.manager import ConfigManager
    from localagentcli.shell.ui import ShellUI
    from localagentcli.storage.manager import StorageManager

    storage = StorageManager()
    storage.initialize()

    first_run = not storage.config_path.exists()

    config = ConfigManager(storage.config_path)
    config.load()

    shell = ShellUI(config=config, storage=storage, first_run=first_run)
    shell.run()


if __name__ == "__main__":
    main()
