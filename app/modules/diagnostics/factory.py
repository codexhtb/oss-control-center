"""Construct optional monitoring readers without inventing unavailable sources."""

import os

from app.modules.diagnostics.config import MappingRegistry, MonitoringConfig, protected_text
from app.modules.diagnostics.correlation import CorrelationEngine
from app.modules.diagnostics.http import JsonTransport
from app.modules.diagnostics.onu import BdcomOnuReader, OnuDbConfig
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


def get_onu_runtime() -> tuple[MappingRegistry, BdcomOnuReader | None]:
    config = MonitoringConfig.from_env()
    registry = MappingRegistry.from_file(config.mapping_file)
    tunnel_password = os.environ.get("BDCOM_ONU_TUNNEL_PASSWORD_FILE")
    if tunnel_password:
        if os.environ.get("BDCOM_ONU_CREDENTIAL_FILE"):
            raise ValueError("BDCOM credential modes are mutually exclusive")
        socket_path = os.environ.get("BDCOM_ONU_TUNNEL_SOCKET", "/run/bdcom/bdcom.sock")
        return registry, BdcomOnuReader(
            OnuDbConfig.from_tunnel_password_file(tunnel_password, socket_path, config.app_env)
        )
    path = os.environ.get("BDCOM_ONU_CREDENTIAL_FILE")
    return registry, BdcomOnuReader(OnuDbConfig.from_file(path, config.app_env)) if path else None
