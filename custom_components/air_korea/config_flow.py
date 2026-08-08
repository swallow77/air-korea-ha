import binascii
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_KEY
from .const import DOMAIN, TITLE, CONF_STATION_NAME
from .coordinator import (
    AirKoreaAPI,
    AirKoreaAuthError,
    AirKoreaError,
    AirKoreaStationError,
)

_LOGGER = logging.getLogger(__name__)

# 사용자 입력 데이터 스키마 정의
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): str,
        vol.Required(CONF_STATION_NAME): str
    }
)


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """기존 설정값을 포함한 입력 스키마를 반환합니다."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_API_KEY, default=defaults.get(CONF_API_KEY, "")): str,
            vol.Required(
                CONF_STATION_NAME,
                default=defaults.get(CONF_STATION_NAME, ""),
            ): str,
        }
    )


# API 인증을 검증하는 비동기 함수
async def async_validate_auth(api: AirKoreaAPI) -> dict[str, Any]:
    """사용자 입력을 검증하여 연결할 수 있는지 확인합니다.
    Data는 STEP_USER_DATA_SCHEMA로부터 키 값을 갖고 있습니다.
    """

    errors = {}
    try:
        await api.async_get()
    except AirKoreaAuthError as err:
        if err.code == "30":
            errors["base"] = "service_key_not_registered"
        elif err.code == "20":
            errors["base"] = "service_access_denied"
        else:
            errors["base"] = "invalid_auth"
    except AirKoreaStationError:
        errors["base"] = "station_not_found"
    except AirKoreaError:
        errors["base"] = "cannot_connect"
    return errors


class AirKoreaConfigFlow(ConfigFlow, domain=DOMAIN):
    """설정 흐름 클래스 정의"""
    VERSION = 1
    _pending_user_input: dict[str, Any] | None = None

    async def _async_create_user_entry(
            self, user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        """고유 측정소 ID를 설정하고 구성 항목을 생성합니다."""
        station_name = user_input[CONF_STATION_NAME].strip()
        user_input[CONF_STATION_NAME] = station_name
        hex_station_name = binascii.hexlify(station_name.encode()).decode()
        await self.async_set_unique_id(f"{DOMAIN}_{hex_station_name}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"{TITLE} - {station_name}", data=user_input
        )

    async def async_step_user(
            self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """초기 단계 처리"""

        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=_schema()
            )

        api_key = user_input[CONF_API_KEY]
        station_name = user_input[CONF_STATION_NAME]
        api = AirKoreaAPI(self.hass, api_key, station_name)

        if errors := await async_validate_auth(api):
            if errors.get("base") == "service_key_not_registered":
                self._pending_user_input = dict(user_input)
                return await self.async_step_activation_pending()
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors=errors,
            )
        return await self._async_create_user_entry(user_input)

    async def async_step_activation_pending(
            self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """포털 동기화 대기 중인 키를 자동 재시도 상태로 등록합니다."""
        if self._pending_user_input is None:
            return self.async_abort(reason="pending_data_missing")

        if user_input is None:
            return self.async_show_form(
                step_id="activation_pending",
                data_schema=vol.Schema({}),
                last_step=True,
            )

        return await self._async_create_user_entry(self._pending_user_input)

    async def async_step_reconfigure(
            self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """승인된 공공데이터포털 키 또는 측정소로 기존 항목을 갱신합니다."""
        entry = self._get_reconfigure_entry()

        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_schema(dict(entry.data)),
            )

        api = AirKoreaAPI(
            self.hass,
            user_input[CONF_API_KEY],
            user_input[CONF_STATION_NAME],
        )
        if errors := await async_validate_auth(api):
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_schema(user_input),
                errors=errors,
            )

        return self.async_update_reload_and_abort(
            entry,
            title=f"{TITLE} - {user_input[CONF_STATION_NAME]}",
            data=user_input,
        )
