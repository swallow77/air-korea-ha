import binascii
import json
import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .const import AIR_KOREA_API_URL, COORDINATOR_UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class AirKoreaError(Exception):
    """에어 코리아 예외의 기본 클래스"""


class AirKoreaAuthError(AirKoreaError):
    """공공데이터포털 서비스키 인증 실패"""


class AirKoreaAPI:
    """에어 코리아 API"""

    def __init__(self, hass: HomeAssistant, api_key: str, station_name: str):
        """에어 코리아 API 초기화"""
        self._session = async_get_clientsession(hass)
        self._api_key = api_key
        self._station_name = station_name

    @staticmethod
    def _api_error(payload: dict[str, Any]) -> tuple[str, str | None]:
        """공공데이터포털의 HTTP/논리 오류 메시지를 추출합니다."""
        openapi_header = payload.get("OpenAPI_ServiceResponse", {}).get("cmmMsgHeader", {})
        if openapi_header:
            return (
                openapi_header.get("returnAuthMsg")
                or openapi_header.get("errMsg")
                or "공공데이터포털 인증 오류",
                openapi_header.get("returnReasonCode"),
            )

        header = payload.get("response", {}).get("header", {})
        return (
            header.get("resultMsg") or "에어코리아 API 응답 오류",
            header.get("resultCode"),
        )

    async def async_get(self) -> dict[str, Any]:
        """API 정보 업데이트를 위한 업데이트 함수"""
        url = f"{AIR_KOREA_API_URL}/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty"
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with self._session.get(
                url,
                params={
                    'pageNo': '1',
                    'numOfRows': '1',
                    'ver': '1.3',
                    'dataTerm': 'daily',
                    'serviceKey': self._api_key,
                    'stationName': self._station_name,
                    'returnType': 'json',
                },
                timeout=timeout,
            ) as response:
                status = response.status
                response_text = await response.text()

            try:
                response_data = json.loads(response_text)
            except json.JSONDecodeError as ex:
                raise AirKoreaError(f"HTTP {status}: JSON 응답이 아닙니다") from ex

            if status != 200:
                message, code = self._api_error(response_data)
                _LOGGER.warning("AirKorea API request failed (HTTP %s, code %s): %s", status, code, message)
                if code in {"20", "21", "22", "30", "31", "32"}:
                    raise AirKoreaAuthError(f"공공데이터포털 서비스키 오류 ({code}): {message}")
                raise AirKoreaError(f"HTTP {status}: {message}")

            message, code = self._api_error(response_data)
            if code and code != "00":
                _LOGGER.warning("AirKorea API returned an error (code %s): %s", code, message)
                if code in {"20", "21", "22", "30", "31", "32"}:
                    raise AirKoreaAuthError(f"공공데이터포털 서비스키 오류 ({code}): {message}")
                raise AirKoreaError(f"API 오류 ({code}): {message}")

            items = response_data.get("response", {}).get("body", {}).get("items") or []
            if isinstance(items, dict):
                items = [items]
            if not items:
                raise AirKoreaError("측정소 데이터를 찾을 수 없습니다. 측정소 이름을 확인하세요.")
            return items[0]
        except AirKoreaError:
            raise
        except aiohttp.ClientError as ex:
            _LOGGER.warning("AirKorea API request failed: %s", type(ex).__name__)
            raise AirKoreaError("에어코리아 API에 연결할 수 없습니다") from ex
        except Exception as ex:
            _LOGGER.error('AirKorea API 상태 업데이트 실패 오류 (%s): %r', type(ex).__name__, ex)
            raise AirKoreaError(str(ex)) from ex


class AirKoreaCoordinator(DataUpdateCoordinator):
    """에어 코리아 데이터 업데이트 코디네이터입니다."""

    def __init__(self, hass: HomeAssistant, api: AirKoreaAPI, station_name: str):
        super().__init__(
            hass,
            _LOGGER,
            name="AirKoreaCoordinator",
            update_interval=COORDINATOR_UPDATE_INTERVAL,
        )
        self._api: AirKoreaAPI = api
        self.station_name: str = binascii.hexlify(station_name.encode()).decode()

    async def _async_update_data(self) -> dict[str, Any]:
        """API 정보 업데이트를 위한 업데이트 함수"""
        try:
            return await self._api.async_get()
        except AirKoreaError as err:
            raise UpdateFailed(str(err)) from err
