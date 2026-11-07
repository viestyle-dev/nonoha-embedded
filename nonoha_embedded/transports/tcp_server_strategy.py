"""TCP server transport strategy (dart_bridge independent).

This file is separate from runtime.py to allow lazy import -
when TCP is not used, this module is not loaded.
"""

import asyncio
import logging
from typing import Callable

from nonoha_embedded.message_protocol import MSG_TYPE_JSON

logger = logging.getLogger(__name__)


class TcpServerStrategy:
    """TCP server transport strategy with auto-restart on failure.

    iOS may invalidate server socket when app goes to background.
    This implementation detects socket errors and restarts the server.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        """Initialize TCP server strategy.

        Args:
            host: Host address to bind to.
            port: Port number to listen on.
        """
        self._host = host
        self._port = port
        self._on_message: Callable[[bytes], bytes | None] | None = None
        self._client_writer: asyncio.StreamWriter | None = None
        self._current_server: asyncio.Server | None = None
        self._restart_requested: bool = False

    async def run(self, on_message: Callable[[bytes], bytes | None]) -> None:
        """Run the TCP server with auto-restart on failure.

        Args:
            on_message: Callback to handle incoming messages.
        """
        self._on_message = on_message
        await self._run_server_loop()

    def send_event(self, data: bytes) -> None:
        """Send an event to the connected client.

        Args:
            data: Event data to send.
        """
        if self._client_writer is None:
            return
        try:
            self._client_writer.write(len(data).to_bytes(4, "big"))
            self._client_writer.write(data)
            # Schedule drain without blocking
            asyncio.create_task(self._drain_writer())
        except Exception as e:
            logger.error(f"send_event error: {e}")

    async def _drain_writer(self) -> None:
        """Drain the writer asynchronously."""
        if self._client_writer:
            try:
                await self._client_writer.drain()
            except Exception:
                pass

    async def _run_server_loop(self) -> None:
        """Run the TCP server with auto-restart on failure."""
        restart_count = 0
        restart_delay = 1.0  # seconds

        logger.info("=== Server loop started ===")

        # Start a heartbeat task to monitor event loop health
        async def heartbeat():
            count = 0
            while True:
                await asyncio.sleep(5)
                count += 1
                logger.info(f"Heartbeat #{count}")

        heartbeat_task = asyncio.create_task(heartbeat())

        try:
            while True:
                should_restart = await self._run_server_once()

                if not should_restart:
                    logger.info("Server exited without restart flag")
                    break

                restart_count += 1
                logger.info(f"=== Server restart #{restart_count} in {restart_delay}s ===")
                await asyncio.sleep(restart_delay)

        except Exception as e:
            logger.info(f"Server loop error: {e}")
            import traceback

            logger.info(traceback.format_exc())

        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            logger.info("=== Server loop exiting ===")

    async def _run_server_once(self) -> bool:
        """Run the TCP server once.

        Returns:
            True if should restart, False to exit.
        """
        server = None
        logger.info("Starting TCP server instance...")
        try:
            server = await asyncio.start_server(
                self._handle_client,
                self._host,
                self._port,
            )
            self._current_server = server

            addr = server.sockets[0].getsockname()
            logger.info(f"TCP server running on {addr[0]}:{addr[1]}")

            async with server:
                logger.info("Entering serve_forever()")
                await server.serve_forever()

            # serve_forever exited (server.close() was called)
            logger.info("serve_forever() exited (server closed)")
            return True  # Restart server

        except asyncio.CancelledError:
            if self._restart_requested:
                logger.info("Server cancelled (restart requested)")
                self._restart_requested = False
                return True  # Restart due to socket error
            else:
                logger.info("Server cancelled (intentional shutdown)")
                return False  # Don't restart on intentional cancellation

        except OSError as e:
            # Socket errors (e.g., iOS invalidated socket after background)
            logger.info(f"Server OSError (will restart): {e}")
            import traceback

            logger.info(traceback.format_exc())
            return True  # Restart server

        except Exception as e:
            logger.info(f"Server error (will restart): {e}")
            import traceback

            logger.info(traceback.format_exc())
            return True  # Restart on other errors

        finally:
            self._current_server = None
            if server is not None:
                try:
                    server.close()
                    await server.wait_closed()
                    logger.info("Server closed")
                except Exception as e:
                    logger.info(f"Error closing server: {e}")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle TCP connection with binary/JSON protocol support."""
        addr = writer.get_extra_info("peername")
        logger.info(f"Client connected: {addr}")

        # Store writer for push events
        self._client_writer = writer

        try:
            while True:
                # Read message length (4 bytes, big-endian)
                length_bytes = await reader.readexactly(4)
                length = int.from_bytes(length_bytes, "big")

                # Read message
                data = await reader.readexactly(length)

                # Handle message via callback
                if self._on_message:
                    response = self._on_message(data)

                    # Send response if any
                    if response is not None:
                        writer.write(len(response).to_bytes(4, "big"))
                        writer.write(response)
                        await writer.drain()

        except asyncio.IncompleteReadError:
            logger.info(f"Client disconnected (IncompleteReadError): {addr}")
        except OSError as e:
            # Socket errors - likely iOS invalidated socket after background
            logger.info(f"Client socket error: {addr} - {e} (errno={e.errno})")
            # Signal server to restart by closing it
            if self._current_server is not None:
                logger.info("Signaling server restart due to socket error")
                self._restart_requested = True
                self._current_server.close()
        except Exception as e:
            logger.info(f"Client error: {addr} - {e}")
            import traceback

            logger.info(traceback.format_exc())
        finally:
            # Clear client writer
            logger.info(f"tcp_handler finally block: cleaning up for {addr}")
            self._client_writer = None
            try:
                writer.close()
                await writer.wait_closed()
                logger.info(f"Writer closed for {addr}")
            except Exception as e:
                logger.info(f"Error closing writer for {addr}: {e}")
            logger.info("tcp_handler exiting, server should accept new connections")
