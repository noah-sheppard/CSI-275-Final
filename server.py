# --- START OF FILE server.py ---

"""Final Project, Threaded TCP Chat.

Author: Noah Sheppard
Class: CSI-275-01
Assignment: Final Project

Certification of Authenticity:
I certify that this is entirely my own work, except where I have given
fully-documented references to the work of others. I understand the definition
and consequences of plagiarism and acknowledge that the assessor of this
assignment may, for the purpose of assessing this assignment:
- Reproduce this assignment and provide a copy to another member of academic
- staff; and/or Communicate a copy of this assignment to a plagiarism checking
- service (which may then retain a copy of this assignment on its database for
- the purpose of future plagiarism checking)
"""

import socket
import threading
import json
import struct
import logging
import time

HOST = '0.0.0.0'
READING_PORT = 65432
WRITING_PORT = 65433
clients = {}
clients_lock = threading.Lock()

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(threadName)s] %(levelname)s: %(message)s',
                    datefmt='%H:%M:%S')

def _close_socket_safely(sock, sock_description="socket"):
    if sock:
        try:
            # sock.shutdown(socket.SHUT_RDWR) # Causes issues if already closed
            sock.close()
            logging.info("%s closed.", sock_description.capitalize())
        except OSError as e:
            logging.warning("Error closing %s: %s", sock_description, e)

def send_message(sock, message_data):
    """Packs and sends a message (list -> JSON -> UTF-8 -> length prefix -> socket)."""
    try:
        encoded_message = json.dumps(message_data).encode('utf-8')
        length_prefix = struct.pack('>I', len(encoded_message))
        sock.sendall(length_prefix)
        sock.sendall(encoded_message)
        return True
    except (socket.error, BrokenPipeError, OSError) as e:
        # Log less verbosely during normal operation like broadcasts
        # logging.warning("Send failed for %s: %s", sock.getpeername() if sock else 'N/A', e)
        return False
    except Exception as e:
        logging.error("Unexpected error sending message: %s", e)
        return False

def receive_message(sock):
    """Receives and unpacks a message (socket -> length prefix -> body -> UTF-8 -> JSON -> list)."""
    try:
        length_prefix = sock.recv(4)
        if not length_prefix or len(length_prefix) < 4:
            return None
        message_length = struct.unpack('>I', length_prefix)[0]

        received_data = b''
        while len(received_data) < message_length:
            chunk_size = min(message_length - len(received_data), 4096)
            chunk = sock.recv(chunk_size)
            if not chunk:
                logging.warning("Connection broken while receiving message body.")
                return None
            received_data += chunk

        return json.loads(received_data.decode('utf-8'))
    except (socket.error, struct.error, json.JSONDecodeError, ConnectionResetError, OSError) as e:
        if isinstance(e, ConnectionResetError):
            logging.info("Connection reset by peer.")
        else:
            logging.error("Receive failed: %s", e)
        return None
    except Exception as e:
        logging.error("Unexpected error receiving message: %s", e)
        return None

def broadcast(message_list, sender_name="Server"):
    """Sends a message to all currently connected clients."""
    msg_type = message_list[0]
    num_clients = 0
    disconnected_clients = []

    with clients_lock:
        num_clients = len(clients)

    log_msg = ""
    if msg_type == "BROADCAST":
        log_text = message_list[2] if len(message_list) > 2 else "(Join/Leave message)"
        log_msg = "Broadcasting '%s' from '%s' to %d clients: '%s%s'" % (
            msg_type, sender_name, num_clients, log_text[:50], '...' if len(log_text)>50 else ''
        )
    elif msg_type == "EXIT":
         log_msg = "Broadcasting '%s' notification for '%s' to %d clients." % (
             msg_type, sender_name, num_clients
         )
    if log_msg:
        logging.info(log_msg)

    with clients_lock:
        client_items = list(clients.items())
        for name, sock in client_items:
            if not send_message(sock, message_list):
                logging.warning("Send failed to '%s' during broadcast. Marking for removal.", name)
                disconnected_clients.append(name)

    for name in disconnected_clients:
        remove_client(name, notify=False)

def send_private(message_list, recipient_name):
    """Sends a private message to a single specific client."""
    msg_type, sender_name, text = message_list[0], message_list[1], message_list[2]

    logging.info("Attempting PM from '%s' to '%s': '%s%s'",
                 sender_name, recipient_name, text[:50], '...' if len(text)>50 else '')

    sock_to_send = None
    recipient_found = False
    with clients_lock:
        if recipient_name in clients:
            sock_to_send = clients[recipient_name]
            recipient_found = True

    if recipient_found and sock_to_send:
        if not send_message(sock_to_send, message_list):
            logging.warning("Send failed for PM to '%s'. Removing client.", recipient_name)
            remove_client(recipient_name, notify=True)
            return False
        logging.info("PM successfully sent to '%s'.", recipient_name)
        return True
    if recipient_found and not sock_to_send:
        logging.error("Logic error: Recipient '%s' found but socket was None during PM send.", recipient_name)
        return False
    logging.warning("PM recipient '%s' not found.", recipient_name)
    return False

def remove_client(screen_name, notify=True):
    """Removes a client from the registry, closes their socket, and optionally broadcasts their departure."""
    sock_to_close = None
    client_was_present = False
    with clients_lock:
        if screen_name in clients:
            sock_to_close = clients.pop(screen_name)
            client_was_present = True
            logging.info("Removing '%s' from active clients.", screen_name)

    if sock_to_close:
        _close_socket_safely(sock_to_close, f"receiving socket for '{screen_name}'")

        if notify and client_was_present:
            logging.info("Broadcasting exit notification for '%s'.", screen_name)
            exit_notification = ["BROADCAST", "Server", f"{screen_name} has left the chat."]
            broadcast(exit_notification, "Server")

def _identify_client(msg, address):
    """Try to identify the client screen name from a message."""
    sender = msg[1]
    client_screen_name = None
    if isinstance(sender, str) and sender:
         with clients_lock:
             if sender in clients:
                 client_screen_name = sender
                 logging.info("Handler identified connection %s as '%s'", address, client_screen_name)
             else:
                 logging.warning("Received message from %s with sender '%s', but name not registered. Ignoring.", address, sender)
    else:
        logging.warning("Received message from %s with invalid sender field: %s. Ignoring.", address, msg)
    return client_screen_name

def _process_client_message(msg, client_screen_name, address):
    """Process a validated message from an identified client."""
    msg_type = msg[0]
    sender = msg[1]

    if sender != client_screen_name:
        logging.warning("Received message from %s claiming to be '%s', but handler associated with '%s'. Ignoring.",
                        address, sender, client_screen_name)
        return

    if msg_type == "BROADCAST" and len(msg) == 3:
        broadcast(msg, client_screen_name)
    elif msg_type == "PRIVATE" and len(msg) == 4:
        recipient = msg[3]
        if not isinstance(recipient, str) or not recipient:
            logging.warning("Received invalid PRIVATE message from '%s': Bad recipient '%s'.", client_screen_name, recipient)
            return
        send_private(msg, recipient)
    elif msg_type == "EXIT" and len(msg) == 2:
         logging.info("Received EXIT command from '%s'. Closing connection.", client_screen_name)
         return "EXIT" # Signal to exit loop
    else:
         logging.warning("Unknown message type '%s' or format from '%s': %s", msg_type, client_screen_name, msg)
    return None # Continue loop

def handle_client_messages(handler_sock, address):
    """Thread target: Handles all messages received FROM a single client via their sending socket."""
    logging.info("Handler started for new connection from %s. Waiting for identification.", address)
    client_screen_name = None
    last_received_msg = None

    try:
        while True:
            msg = receive_message(handler_sock)
            last_received_msg = msg

            if msg is None:
                logging.info("Connection closed by %s.", client_screen_name or address)
                break

            if not isinstance(msg, list) or len(msg) < 2:
                logging.warning("Received malformed message from %s: %s", client_screen_name or address, msg)
                continue

            if client_screen_name is None:
                client_screen_name = _identify_client(msg, address)
                if client_screen_name is None:
                    continue # Keep waiting for identification if failed

            action = _process_client_message(msg, client_screen_name, address)
            if action == "EXIT":
                break

    except (socket.error, OSError) as e:
        logging.error("Network error in handler for %s: %s", client_screen_name or address, e)
    except Exception as e:
        logging.error("Exception in handler for %s: %s", client_screen_name or address, e, exc_info=True)
    finally:
        logging.info("Handler stopping for %s.", client_screen_name or address)
        _close_socket_safely(handler_sock, f"sending socket for {client_screen_name or address}")

        if client_screen_name:
            was_exit_cmd = (isinstance(last_received_msg, list) and
                            len(last_received_msg) == 2 and
                            last_received_msg[0] == "EXIT" and
                            last_received_msg[1] == client_screen_name)
            remove_client(client_screen_name, notify=not was_exit_cmd)


def reading_server(host, port):
    """Main thread target: Listens for new client connections on the READING_PORT."""
    listen_sock = None
    try:
        listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_sock.bind((host, port))
        listen_sock.listen()
        logging.info("Reading server listening on %s:%s for client sending sockets", host, port)

        while True:
            try:
                client_sock, address = listen_sock.accept()
                logging.info("Accepted SENDING connection from %s", address)

                thread = threading.Thread(
                    target=handle_client_messages,
                    args=(client_sock, address),
                    daemon=True,
                    name=f"Handler-{address[1]}"
                )
                thread.start()
                logging.info("Started handler thread for %s", address)

            except OSError as e:
                 logging.info("Reading server socket closed (%s). Stopping accept loop.", e)
                 break
            except Exception as e:
                 logging.error("Error accepting connection in reading thread: %s", e)
                 time.sleep(1)
    except Exception as e:
        logging.critical("Reading server failed to initialize on %s:%s: %s", host, port, e, exc_info=True)
    finally:
        _close_socket_safely(listen_sock, "Reading server listening socket")
        logging.info("Reading server thread finished.")

def _validate_start_message(msg):
    """Checks if a message is a valid START message."""
    return (msg and isinstance(msg, list) and len(msg) == 2 and
            msg[0] == "START" and isinstance(msg[1], str) and msg[1])

def _handle_registration(registration_sock, address):
    """Handles the START message and client registration logic."""
    msg = receive_message(registration_sock)
    client_added = False
    screen_name_to_add = None

    if _validate_start_message(msg):
        screen_name_to_add = msg[1]
        with clients_lock:
            if screen_name_to_add in clients:
                logging.warning("Client registration rejected: '%s' from %s (Name taken)", screen_name_to_add, address)
                send_message(registration_sock, ["START_FAIL", "Server", "Screen name is already taken."])
                _close_socket_safely(registration_sock, f"rejected registration socket from {address}")
            else:
                clients[screen_name_to_add] = registration_sock
                logging.info("Client registered successfully: '%s' from %s", screen_name_to_add, address)
                client_added = True
                # Keep registration_sock open for this client

        if client_added:
             join_notification = ["BROADCAST", "Server", f"{screen_name_to_add} has joined the chat!"]
             broadcast(join_notification, "Server")
    else:
        logging.warning("Invalid START message received from %s. Closing connection. Message: %s", address, msg)
        _close_socket_safely(registration_sock, f"invalid START socket from {address}")


def writing_server(host, port):
    """Main thread target: Listens on WRITING_PORT, handles START message and registration."""
    listen_sock = None
    try:
        listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_sock.bind((host, port))
        listen_sock.listen()
        logging.info("Writing server listening on %s:%s for client receiving sockets", host, port)

        while True:
            recv_sock = None
            address = None
            try:
                recv_sock, address = listen_sock.accept()
                logging.info("Accepted RECEIVING connection from %s. Awaiting START.", address)
                _handle_registration(recv_sock, address)

            except OSError as e:
                logging.info("Writing server socket closed (%s). Stopping accept loop.", e)
                break # Exit accept loop
            except Exception as e:
                logging.error("Error handling registration connection from %s in writing thread: %s", address or 'N/A', e)
                _close_socket_safely(recv_sock, f"failed registration socket from {address or 'N/A'}")
                time.sleep(1)

    except Exception as e:
         logging.critical("Writing server failed to initialize on %s:%s: %s", host, port, e, exc_info=True)
    finally:
         _close_socket_safely(listen_sock, "Writing server listening socket")
         logging.info("Writing server shutting down. Cleaning up remaining client sockets.")
         with clients_lock:
             client_items = list(clients.items())
             for name, sock in client_items:
                 logging.info("Closing socket for '%s' during shutdown.", name)
                 _close_socket_safely(sock, f"socket for {name}")
             clients.clear()
         logging.info("Writing server thread finished.")


# --- Main Execution ---
if __name__ == "__main__":
    logging.info("Starting chat server on host: %s", HOST)

    write_thread = threading.Thread(target=writing_server,
                                    args=(HOST, WRITING_PORT),
                                    daemon=True, name="WritingThread")
    read_thread = threading.Thread(target=reading_server,
                                   args=(HOST, READING_PORT),
                                   daemon=True, name="ReadingThread")

    write_thread.start()
    read_thread.start()

    logging.info("Server running [Send-to Port: %s, Receive-from Port: %s]. Press Ctrl+C to stop.", READING_PORT, WRITING_PORT)

    try:
        while write_thread.is_alive() and read_thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Ctrl+C received. Initiating server shutdown...")
    except Exception as e:
        logging.critical("Server main loop encountered an unexpected error: %s", e)
    finally:
        logging.info("Server shutdown sequence complete.")
        print("Server stopped.")
