"""Construct optional monitoring readers without inventing unavailable sources."""

from app.modules.diagnostics.config import MappingRegistry, MonitoringConfig, protected_text
from app.modules.diagnostics.correlation import CorrelationEngine
from app.modules.diagnostics.http import JsonTransport
from app.modules.diagnostics.providers import IcingaProvider, LibreNMSProvider


def get_correlation_engine() -> CorrelationEngine:
    config = MonitoringConfig.from_env()
    registry = MappingRegistry.from_file(config.mapping_file)
    librenms = None
    icinga = None
    librenms_url = config.validate_endpoint(config.librenms_url)
    icinga_url = config.validate_endpoint(config.icinga_url)
    if librenms_url and config.librenms_token_file:
        token = protected_text(config.librenms_token_file)
        librenms = LibreNMSProvider(
            JsonTransport(
                librenms_url,
                {"Authorization": f"Bearer {token}"},
                config.timeout_seconds,
                config.ca_file,
            )
        )
    if icinga_url and config.icinga_user and config.icinga_password_file:
        password = protected_text(config.icinga_password_file)
        icinga = IcingaProvider(
            JsonTransport(
                icinga_url,
                {"Authorization": IcingaProvider.authorization(config.icinga_user, password)},
                config.timeout_seconds,
                config.ca_file,
            )
        )
    return CorrelationEngine(registry, librenms, icinga)
