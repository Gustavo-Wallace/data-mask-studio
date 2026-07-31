from data_mask_studio.backup.models import EnvironmentPaths
from data_mask_studio.profiles import default_profiles_path
from data_mask_studio.security.key_provider import KEY_FILE_NAME
from data_mask_studio.vault.database import default_database_path, default_vault_directory
from data_mask_studio.vault.key_provider import VAULT_KEY_FILE_NAME


def default_environment_paths() -> EnvironmentPaths:
    directory = default_vault_directory()
    return EnvironmentPaths(
        directory=directory,
        hmac_key_path=directory / KEY_FILE_NAME,
        vault_key_path=directory / VAULT_KEY_FILE_NAME,
        vault_database_path=default_database_path(),
        profiles_path=default_profiles_path(),
    )
