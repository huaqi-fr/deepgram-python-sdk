# Copyright 2023-2024 Deepgram SDK contributors. All Rights Reserved.
# Use of this source code is governed by a MIT license that can be found in the LICENSE file.
# SPDX-License-Identifier: MIT
import json
from websockets.sync.client import connect
import websockets
import threading
import time
import logging, verboselogs

from ....options import DeepgramClientOptions
from ..enums import LiveTranscriptionEvents
from ..helpers import convert_to_websocket_url, append_query_params
from ..errors import DeepgramError, DeepgramWebsocketError

from .response import (
    LiveResultResponse,
    MetadataResponse,
    SpeechStartedResponse,
    UtteranceEndResponse,
    ErrorResponse,
)
from .options import LiveOptions

PING_INTERVAL = 5


class LiveClient:
    """
    Client for interacting with Deepgram's live transcription services over WebSockets.

     This class provides methods to establish a WebSocket connection for live transcription and handle real-time transcription events.

     Args:
         config (DeepgramClientOptions): all the options for the client.
    """

    def __init__(self, config: DeepgramClientOptions):
        if config is None:
            raise DeepgramError("Config are required")

        self.logger = logging.getLogger(__name__)
        self.logger.addHandler(logging.StreamHandler())
        self.logger.setLevel(config.verbose)

        self.config = config
        self.endpoint = "v1/listen"
        self._socket = None
        self.exit = False
        self._event_handlers = {event: [] for event in LiveTranscriptionEvents}
        self.websocket_url = convert_to_websocket_url(self.config.url, self.endpoint)

    def start(
        self,
        options: LiveOptions = None,
        addons: dict = None,
        members: dict = None,
        **kwargs,
    ):
        """
        Starts the WebSocket connection for live transcription.
        """
        self.logger.debug("LiveClient.start ENTER")
        self.logger.info("kwargs: %s", options)
        self.logger.info("addons: %s", addons)
        self.logger.info("members: %s", members)
        self.logger.info("options: %s", kwargs)

        if options is not None and not options.check():
            self.logger.error("options.check failed")
            self.logger.debug("LiveClient.start LEAVE")
            raise DeepgramError("Fatal transcription options error")

        if self._socket is not None:
            self.logger.error("socket is already initialized")
            self.logger.debug("LiveClient.start LEAVE")
            raise DeepgramWebsocketError("Websocket already started")

        self.options = options
        self.addons = addons

        # add "members" as members of the class
        if members is not None:
            self.__dict__.update(members)

        # set kwargs as members of the class
        if kwargs is not None:
            self.kwargs = kwargs
        else:
            self.kwargs = dict()

        if isinstance(options, LiveOptions):
            self.logger.info("LiveOptions switching class -> json")
            self.options = self.options.to_dict()

        combined_options = dict(self.options)
        if addons is not None:
            self.logger.info("merging addons to options")
            combined_options.update(addons)
            self.logger.info("new options: %s", combined_options)
        self.logger.debug("combined_options: %s", combined_options)

        url_with_params = append_query_params(self.websocket_url, combined_options)
        self._socket = connect(url_with_params, additional_headers=self.config.headers)

        self.exit = False
        self.lock_exit = threading.Lock()
        self.lock_send = threading.Lock()

        # listening thread
        self.listening = threading.Thread(target=self._listening)
        self.listening.start()

        # keepalive thread
        self.processing = threading.Thread(target=self._processing)
        self.processing.start()

        self.logger.notice("start succeeded")
        self.logger.debug("LiveClient.start LEAVE")

    def on(self, event, handler):
        """
        Registers event handlers for specific events.
        """
        self.logger.info("event fired: %s", event)
        if event in LiveTranscriptionEvents and callable(handler):
            self._event_handlers[event].append(handler)

    def _emit(self, event, *args, **kwargs):
        for handler in self._event_handlers[event]:
            handler(self, *args, **kwargs)

    def _listening(self) -> None:
        self.logger.debug("LiveClient._listening ENTER")

        while True:
            try:
                message = self._socket.recv()
                if len(message) == 0:
                    self.logger.info("message is empty")
                    continue

                with self.lock_exit:
                    myExit = self.exit

                if myExit:
                    self.logger.notice("_listening exiting gracefully")
                    self.logger.debug("LiveClient._listening LEAVE")
                    return

                data = json.loads(message)
                response_type = data.get("type")
                self.logger.debug("response_type: %s, data: %s", response_type, data)

                match response_type:
                    case LiveTranscriptionEvents.Transcript.value:
                        result = LiveResultResponse.from_json(message)
                        self.logger.verbose("LiveResultResponse: %s", result)
                        self._emit(
                            LiveTranscriptionEvents.Transcript,
                            result=result,
                            **dict(self.kwargs),
                        )
                    case LiveTranscriptionEvents.Metadata.value:
                        result = MetadataResponse.from_json(message)
                        self.logger.verbose("MetadataResponse: %s", result)
                        self._emit(
                            LiveTranscriptionEvents.Metadata,
                            metadata=result,
                            **dict(self.kwargs),
                        )
                    case LiveTranscriptionEvents.SpeechStarted.value:
                        result = SpeechStartedResponse.from_json(message)
                        self.logger.verbose("SpeechStartedResponse: %s", result)
                        self._emit(
                            LiveTranscriptionEvents.SpeechStarted,
                            speech_started=result,
                            **dict(self.kwargs),
                        )
                    case LiveTranscriptionEvents.UtteranceEnd.value:
                        result = UtteranceEndResponse.from_json(message)
                        self.logger.verbose("UtteranceEndResponse: %s", result)
                        self._emit(
                            LiveTranscriptionEvents.UtteranceEnd,
                            utterance_end=result,
                            **dict(self.kwargs),
                        )
                    case LiveTranscriptionEvents.Error.value:
                        result = ErrorResponse.from_json(message)
                        self.logger.verbose("ErrorResponse: %s", result)
                        self._emit(
                            LiveTranscriptionEvents.Error,
                            error=result,
                            **dict(self.kwargs),
                        )
                    case _:
                        self.logger.warning(
                            "Unknown Message: response_type: %s, data: %s",
                            response_type,
                            data,
                        )

            except websockets.exceptions.ConnectionClosedOK as e:
                self.logger.notice(f"_listening({e.code}) exiting gracefully")

                # signal exit and close
                self.signal_exit()

                self.logger.debug("LiveClient._listening LEAVE")
                return

            except websockets.exceptions.ConnectionClosedError as e:
                error: ErrorResponse = {
                    "type": "Exception",
                    "description": "ConnectionClosedError in _listening",
                    "message": f"{e}",
                    "variant": "",
                }
                self.logger.error(
                    f"WebSocket connection closed with code {e.code}: {e.reason}"
                )
                self._emit(LiveTranscriptionEvents.Error, error)

                # signal exit and close
                self.signal_exit()

                self.logger.debug("LiveClient._listening LEAVE")

                if (
                    "termination_exception" in self.options
                    and self.options["termination_exception"] == "true"
                ):
                    raise
                return

            except Exception as e:
                error: ErrorResponse = {
                    "type": "Exception",
                    "description": "Exception in _listening",
                    "message": f"{e}",
                    "variant": "",
                }
                self._emit(LiveTranscriptionEvents.Error, error)
                self.logger.error("Exception in _listening: %s", str(e))

                # signal exit and close
                self.signal_exit()

                self.logger.debug("LiveClient._listening LEAVE")
                if (
                    "termination_exception" in self.options
                    and self.options["termination_exception"] == "true"
                ):
                    raise
                return

    def _processing(self) -> None:
        self.logger.debug("LiveClient._processing ENTER")

        counter = 0

        while True:
            try:
                time.sleep(PING_INTERVAL)
                counter += 1

                with self.lock_exit:
                    myExit = self.exit

                if myExit:
                    self.logger.notice("_processing exiting gracefully")
                    self.logger.debug("LiveClient._processing LEAVE")
                    return

                # deepgram keepalive
                if self.config.options.get("keepalive") == "true":
                    self.logger.debug("Sending KeepAlive...")
                    self.send(json.dumps({"type": "KeepAlive"}))

                # websocket keepalive
                if counter % 4 == 0:
                    self.logger.debug("Sending Ping...")
                    self.send_ping()

            except websockets.exceptions.ConnectionClosedOK as e:
                self.logger.notice("_processing({e.code}) exiting gracefully")

                # signal exit and close
                self.signal_exit()

                self.logger.debug("LiveClient._processing LEAVE")
                return

            except websockets.exceptions.ConnectionClosedError as e:
                error: ErrorResponse = {
                    "type": "Exception",
                    "description": "ConnectionClosedError in _processing",
                    "message": f"{e}",
                    "variant": "",
                }
                self.logger.error(
                    f"WebSocket connection closed with code {e.code}: {e.reason}"
                )
                self._emit(LiveTranscriptionEvents.Error, error)

                # signal exit and close
                self.signal_exit()

                self.logger.debug("LiveClient._processing LEAVE")

                if (
                    "termination_exception" in self.options
                    and self.options["termination_exception"] == "true"
                ):
                    raise
                return

            except Exception as e:
                error: ErrorResponse = {
                    "type": "Exception",
                    "description": "Exception in _processing",
                    "message": f"{e}",
                    "variant": "",
                }
                self._emit(LiveTranscriptionEvents.Error, error)
                self.logger.error("Exception in _processing: %s", str(e))

                # signal exit and close
                self.signal_exit()

                self.logger.debug("LiveClient._processing LEAVE")

                if (
                    "termination_exception" in self.options
                    and self.options["termination_exception"] == "true"
                ):
                    raise
                return

    def send(self, data) -> int:
        """
        Sends data over the WebSocket connection.
        """
        self.logger.spam("LiveClient.send ENTER")
        self.logger.spam("data: %s", data)

        if self._socket:
            with self.lock_send:
                ret = self._socket.send(data)

            self.logger.spam(f"send bytes: {ret}")
            self.logger.spam("LiveClient.send LEAVE")
            return ret

        self.logger.spam("message is empty")
        self.logger.spam("LiveClient.send LEAVE")
        return 0

    def send_ping(self) -> None:
        """
        Sends a ping over the WebSocket connection.
        """
        self.logger.spam("LiveClient.send_ping ENTER")

        if self._socket:
            with self.lock_send:
                self._socket.ping()

        self.logger.spam("LiveClient.send_ping LEAVE")

    def finish(self):
        """
        Closes the WebSocket connection gracefully.
        """
        self.logger.spam("LiveClient.finish ENTER")

        if self._socket:
            self.logger.notice("sending CloseStream...")
            self.send(json.dumps({"type": "CloseStream"}))
            time.sleep(0.5)

        # signal exit
        self.signal_exit()

        if self.processing is not None:
            self.processing.join()
            self.processing = None
        self.logger.notice("processing thread joined")

        if self.listening is not None:
            self.listening.join()
            self.listening = None
        self.logger.notice("listening thread joined")

        if self._socket is not None:
            self.logger.notice("closing socket...")
            self._socket.close()

        self._socket = None
        self.lock_exit = None
        self.lock_send = None

        self.logger.notice("finish succeeded")
        self.logger.spam("LiveClient.finish LEAVE")

    def signal_exit(self):
        # signal exit
        with self.lock_exit:
            self.logger.notice("signal exit")
            self.exit = True

        self.logger.notice("closing socket...")
        if self._socket:
            self.logger.debug("calling socket.close()")
            self._socket.close()
        self._socket = None
