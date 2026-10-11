"""Config flow for ZeroMOUSE."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import ZeroMouseApi, ZeroMouseApiError, ZeroMouseAuthError
from .const import CONF_GIF_SLOWDOWN, DEFAULT_GIF_SLOWDOWN, DOMAIN, MAX_GIF_SLOWDOWN

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)
REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)


class ZeroMouseConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ZeroMOUSE."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return ZeroMouseOptionsFlow()

    async def _async_validate(
        self, email: str, password: str
    ) -> tuple[str | None, dict[str, str]]:
        """Try to log in; return (user_id, errors)."""
        api = ZeroMouseApi(async_get_clientsession(self.hass), email, password)
        try:
            user_id = await api.async_login()
        except ZeroMouseAuthError:
            return None, {"base": "invalid_auth"}
        except ZeroMouseApiError:
            return None, {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected error while logging in to ZeroMOUSE")
            return None, {"base": "unknown"}
        return user_id, {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the ZeroMOUSE account credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            user_id, errors = await self._async_validate(
                email, user_input[CONF_PASSWORD]
            )
            if user_id:
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=email,
                    data={CONF_EMAIL: email, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication when the stored password stops working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password."""
        reauth_entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            user_id, errors = await self._async_validate(
                reauth_entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if user_id:
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            description_placeholders={"email": reauth_entry.data[CONF_EMAIL]},
            errors=errors,
        )


class ZeroMouseOptionsFlow(OptionsFlow):
    """Let the user tune the event GIFs."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options form."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_GIF_SLOWDOWN: int(user_input[CONF_GIF_SLOWDOWN])}
            )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_GIF_SLOWDOWN,
                    default=self.config_entry.options.get(
                        CONF_GIF_SLOWDOWN, DEFAULT_GIF_SLOWDOWN
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=MAX_GIF_SLOWDOWN,
                        step=5,
                        unit_of_measurement="%",
                        mode=NumberSelectorMode.SLIDER,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
