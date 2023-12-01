# Copyright 2023 Deepgram SDK contributors. All Rights Reserved.
# Use of this source code is governed by a MIT license that can be found in the LICENSE file.
# SPDX-License-Identifier: MIT

from typing import Optional
from importlib import import_module
import logging, verboselogs

from .clients.listen import ListenClient, PreRecordedClient
from .clients.manage.client import ManageClient
from .clients.onprem.client import OnPremClient

from .options import DeepgramClientOptions
from .errors import DeepgramApiKeyError, DeepgramModuleError


class DeepgramClient:
    """
    Represents a client for interacting with the Deepgram API.

    This class provides a client for making requests to the Deepgram API with various configuration options.

    Attributes:
        api_key (str): The Deepgram API key used for authentication.
        config_options (DeepgramClientOptions): An optional configuration object specifying client options.

    Raises:
        DeepgramApiKeyError: If the API key is missing or invalid.

    Methods:
        listen: Returns a ListenClient instance for interacting with Deepgram's transcription services.
        manage: Returns a ManageClient instance for managing Deepgram resources.
        onprem: Returns an OnPremClient instance for interacting with Deepgram's on-premises API.
    """

    def __init__(self, api_key: str, config: Optional[DeepgramClientOptions] = None):
        verboselogs.install()
        self.logger = logging.getLogger(__name__)
        self.logger.addHandler(logging.StreamHandler())

        if not api_key:
            raise DeepgramApiKeyError("Deepgram API key is required")

        self.api_key = api_key
        if config is None:  # Use default configuration
            self.config = DeepgramClientOptions(self.api_key)
        else:
            config.set_apikey(self.api_key)
            self.config = config

        self.logger.setLevel(logging.SPAM)

    @property
    def listen(self):
        return ListenClient(self.config)

    @property
    def manage(self):
        return self.Version(self.config, "manage")

    @property
    def onprem(self):
        return self.Version(self.config, "onprem")

    # INTERNAL CLASSES
    class Version:
        def __init__(self, config, parent: str):
            self.logger = logging.getLogger(__name__)
            self.logger.addHandler(logging.StreamHandler())
            self.logger.setLevel(config.verbose)
            self.config = config
            self.parent = parent

        # FUTURE VERSIONING:
        # When v2 or v1.1beta1 or etc. This allows easy access to the latest version of the API.
        # @property
        # def latest(self):
        #     match self.parent:
        #         case "manage":
        #             return ManageClient(self.config)
        #         case "onprem":
        #             return OnPremClient(self.config)
        #         case _:
        #             raise DeepgramModuleError("Invalid parent")

        def v(self, version: str = ""):
            self.logger.debug("Version.v ENTER")
            self.logger.info("version: %s", version)
            if len(version) == 0:
                self.logger.error("version is empty")
                self.logger.debug("Version.v LEAVE")
                raise DeepgramModuleError("Invalid module version")

            className = ""
            match self.parent:
                case "manage":
                    className = "ManageClient"
                case "onprem":
                    className = "OnPremClient"
                case _:
                    self.logger.error("parent unknown: %s", self.parent)
                    self.logger.debug("Version.v LEAVE")
                    raise DeepgramModuleError("Invalid parent type")

            # create class path
            path = f"deepgram.clients.{self.parent}.v{version}.client"
            self.logger.info("path: %s", path)
            self.logger.info("className: %s", className)

            # import class
            mod = import_module(path)
            if mod is None:
                self.logger.error("module path is None")
                self.logger.debug("Version.v LEAVE")
                raise DeepgramModuleError("Unable to find package")

            my_class = getattr(mod, className)
            if my_class is None:
                self.logger.error("my_class is None")
                self.logger.debug("Version.v LEAVE")
                raise DeepgramModuleError("Unable to find class")

            # instantiate class
            myClass = my_class(self.config)
            self.logger.notice("Version.v succeeded")
            self.logger.debug("Version.v LEAVE")
            return myClass