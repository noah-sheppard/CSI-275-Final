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
import sys
import time

# Cleaner logging setup - INFO level is usually sufficient
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(threadName)s] %(levelname)s: %(message)s',
                    datefmt='%H:%M:%S') # Use HH:MM:SS for time

# --- Protocol Helpers (Essential - Unchanged) ---
def send_message(sock, message_data):
    """Packs and sends a message (list -> JSON -> UTF-8 -> length prefix -> socket)."""
    try:
        encoded_message = json.dumps(message_data).encode('utf-8')
        length_prefix = struct.pack('>I', len(encoded_message))
        sock.sendall(length_prefix)
        sock.sendall(encoded_message)
        return True
    except (socket.error, BrokenPipeError, OSError):
        # Keep minimal error logging for send failures
        # logging.error(f"Send failed to {sock.getpeername() if sock else 'N/A'}: {e}") # Too verbose
        return False # Let caller handle outcome
    except Exception as e:
        logging.error(f"Unexpected error sending message: {e}")
        return False

def receive_message(sock):
    """Receives and unpacks a message (socket -> length prefix -> body -> UTF-8 -> JSON -> list)."""
    try:
        length_prefix = sock.recv(4)
        if not length_prefix or len(length_prefix) < 4:
            return None # Connection closed or error
        message_length = struct.unpack('>I', length_prefix)[0]

        received_data = b''
        while len(received_data) < message_length:
            chunk = sock.recv(min(message_length - len(received_data), 4096))
            if not chunk: return None # Connection closed during read
            received_data += chunk

        return json.loads(received_data.decode('utf-8'))
    except (socket.error, struct.error, json.JSONDecodeError, ConnectionResetError, OSError):
        # Log minimally on receive error - often just indicates disconnect
        # logging.error(f"Receive failed/disconnect: {e}") # Can be noisy
        return None
    except Exception as e:
        logging.error(f"Unexpected error receiving message: {e}")
        return None
# --- End Protocol Helpers ---

# --- Server Config & State ---
HOST = '0.0.0.0'
READING_PORT = 65432
WRITING_PORT = 65433
clients = {} # {screen_name: receiving_socket}
clients_lock = threading.Lock()

# --- Core Server Logic ---
def broadcast(message_list, sender_name="Server"):
    """Sends message to all clients, cleaning up failed connections."""
    msg_type = message_list[0]
    # Log high-level intent
    logging.info(f"Broadcasting {msg_type} from {sender_name} to {len(clients)} client(s).")
    disconnected_clients = []
    with clients_lock:
        client_items = list(clients.items()) # Iterate over a copy
        for name, sock in client_items:
            if not send_message(sock, message_list):
                logging.warning(f"Send failed during broadcast to '{name}'. Marking for removal.")
                disconnected_clients.append(name) # Mark for removal

    # Remove disconnected outside lock iteration
    for name in disconnected_clients:
        # Let remove_client handle logging and socket closing
        remove_client(name, notify=False) # Don't double-notify exit

def send_private(message_list, recipient_name):
    """Sends message to a single client."""
    sender_name = message_list[1] # Assume sender is second element
    logging.info(f"Attempting PM from '{sender_name}' to '{recipient_name}'.")
    sock_to_send = None
    recipient_found = False
    with clients_lock:
        if recipient_name in clients:
            sock_to_send = clients[recipient_name]
            recipient_found = True

    if sock_to_send:
        if not send_message(sock_to_send, message_list):
            logging.warning(f"Send failed for PM to '{recipient_name}'. Removing client.")
            # remove_client handles logging, closing, and notification
            remove_client(recipient_name, notify=True)
            return False # Send failed
        else:
            # logging.info(f"PM successfully sent to '{recipient_name}'.") # Optional success log
            return True # Send succeeded
    elif recipient_found:
        # Should not happen if lock logic is correct, but log if it does
         logging.error(f"Logic error: Recipient '{recipient_name}' found but socket was None.")
         return False
    else:
        logging.warning(f"PM recipient '{recipient_name}' not found.")
        # Optionally: Send failure message back to sender (complex)
        return False # Recipient not found

def remove_client(screen_name, notify=True):
    """Removes client socket and optionally notifies others."""
    sock_to_close = None
    client_was_present = False
    with clients_lock:
        if screen_name in clients:
            sock_to_close = clients.pop(screen_name)
            client_was_present = True

    if sock_to_close:
        logging.info(f"Removing client '{screen_name}'.")
        try:
            sock_to_close.close()
        except OSError as e:
            logging.error(f"Error closing socket for {screen_name}: {e}")
        # Notify remaining clients if required
        if notify and client_was_present: # Only notify if removal happened and notification desired
            # Use a temporary list for the message
            exit_notification = ["EXIT", screen_name]
            broadcast(exit_notification, "Server") # Broadcast handles its own logging
    elif client_was_present:
         logging.warning(f"Client '{screen_name}' existed in dict but socket was already None/removed.")
    # else: No need to log if attempting to remove already gone client

def handle_client_messages(send_sock, address):
    """Thread target for handling messages FROM a single client's sending socket."""
    logging.info(f"Handler started for sending socket {address}")
    client_screen_name = None
    last_received_msg = None # Store last msg for finally block context
    try:
        while True:
            msg = receive_message(send_sock)
            last_received_msg = msg

            if msg is None:
                # Log disconnect based on whether client was identified
                logging.info(f"Connection closed by {client_screen_name or address}.")
                break

            # Log raw message minimally for tracing, use DEBUG if too noisy
            # logging.debug(f"Raw msg from {client_screen_name or address}: {msg}")

            # Basic validation
            if not isinstance(msg, list) or len(msg) < 2:
                logging.warning(f"Invalid msg format from {client_screen_name or address}: {msg}")
                continue

            msg_type = msg[0]
            sender = msg[1]

            # Identify client on first valid message
            if client_screen_name is None:
                if isinstance(sender, str) and sender:
                     with clients_lock:
                         if sender in clients: # Check if name is registered by WritingThread
                             client_screen_name = sender
                             logging.info(f"Identified sending socket {address} as '{client_screen_name}'")
                         else:
                             logging.warning(f"Sender '{sender}' from {address} not registered. Ignoring message.")
                             continue
                else:
                     logging.warning(f"Invalid sender in first message from {address}. Msg: {msg}")
                     continue

            # Sanity check: ensure message sender matches identified client
            if sender != client_screen_name:
                 logging.warning(f"Msg sender '{sender}' != identified client '{client_screen_name}'. Ignoring.")
                 continue

            # --- Process message types (log action summary) ---
            if msg_type == "BROADCAST" and len(msg) == 3:
                # Log processing intent, broadcast() logs sending intent
                logging.info(f"Processing BROADCAST from '{client_screen_name}'.")
                broadcast(msg, client_screen_name)
            elif msg_type == "PRIVATE" and len(msg) == 4:
                recipient = msg[3]
                # Log processing intent, send_private() logs sending intent/outcome
                logging.info(f"Processing PRIVATE from '{client_screen_name}' to '{recipient}'.")
                send_private(msg, recipient)
            elif msg_type == "EXIT" and len(msg) == 2:
                 logging.info(f"Processing EXIT from '{client_screen_name}'.")
                 break # Exit loop, finally handles cleanup
            else:
                logging.warning(f"Unknown type or invalid format from '{client_screen_name}'. Msg: {msg}")

    except Exception as e:
        logging.error(f"Exception in handler for {client_screen_name or address}: {e}", exc_info=False) # Keep brief
    finally:
        logging.info(f"Handler stopping for {client_screen_name or address}.")
        if send_sock:
             try: send_sock.close()
             except OSError: pass
        # Remove client if identified, notify if not a clean EXIT cmd
        if client_screen_name:
            was_exit_cmd = (last_received_msg and isinstance(last_received_msg, list) and
                            len(last_received_msg) == 2 and last_received_msg[0] == "EXIT")
            remove_client(client_screen_name, notify=not was_exit_cmd)
        # No need to log if handler exits before identification, covered by disconnect log

def reading_server(host, port):
    """Listens for client SENDING sockets and starts handler threads."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listen_sock:
            listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listen_sock.bind((host, port))
            listen_sock.listen()
            logging.info(f"Reading server listening on {host}:{port}")
            while True: # Main accept loop
                    try:
                        client_sock, address = listen_sock.accept()
                        # Log acceptance clearly
                        logging.info(f"Accepted SENDING connection from {address}")
                        # Start handler thread
                        thread = threading.Thread(target=handle_client_messages,
                                                  args=(client_sock, address),
                                                  daemon=True,
                                                  name=f"Handler-{address[1]}") # Name thread by port
                        thread.start()
                    except OSError:
                         logging.info("Reading server socket closed, stopping accept loop.")
                         break # Stop loop if listen socket closed
                    except Exception as e:
                         logging.error(f"Error accepting connection in reading thread: {e}")
                         time.sleep(1) # Avoid busy-loop on persistent accept errors
    except Exception as e:
        logging.critical(f"Reading server failed to initialize: {e}", exc_info=True)
    finally:
        logging.info("Reading server thread finished.")


def writing_server(host, port):
    """Listens for client RECEIVING sockets, handles START, registers clients."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listen_sock:
            listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listen_sock.bind((host, port))
            listen_sock.listen()
            logging.info(f"Writing server listening on {host}:{port}")
            while True: # Main accept loop
                recv_sock = None
                address = None
                try:
                    # logging.info("Writing server waiting to accept...") # Too noisy
                    recv_sock, address = listen_sock.accept()
                    logging.info(f"Accepted RECEIVING connection from {address}")

                    msg = receive_message(recv_sock)
                    # logging.info(f"Received initial msg from {address}: {msg}") # Can be noisy

                    # Validate START message
                    if msg and isinstance(msg, list) and len(msg) == 2 and \
                       msg[0] == "START" and isinstance(msg[1], str) and msg[1]:
                        screen_name_to_add = msg[1]
                        client_added = False
                        with clients_lock:
                            if screen_name_to_add in clients:
                                logging.warning(f"Screen name '{screen_name_to_add}' taken. Rejecting {address}.")
                                send_message(recv_sock, ["START_FAIL", "Server", "Screen name taken."])
                            else:
                                clients[screen_name_to_add] = recv_sock
                                logging.info(f"Registered client '{screen_name_to_add}' from {address}")
                                client_added = True

                        # Broadcast join outside lock
                        if client_added:
                             broadcast(["BROADCAST", "Server", f"{screen_name_to_add} has joined!"])
                        elif recv_sock: # Close rejected socket outside lock
                             try: recv_sock.close()
                             except OSError: pass

                    else: # Invalid or non-START message
                        logging.warning(f"Invalid/missing START from {address}. Msg={msg}. Closing.")
                        if recv_sock:
                            try: recv_sock.close()
                            except OSError: pass

                except OSError:
                    logging.info("Writing server socket closed, stopping accept loop.")
                    break # Stop loop if listen socket closed
                except Exception as e:
                    logging.error(f"Error handling connection {address or 'N/A'} in writing thread: {e}")
                    if recv_sock: # Attempt to close problematic socket
                        try: recv_sock.close()
                        except OSError: pass
                    time.sleep(1) # Avoid busy-loop

    except Exception as e:
         logging.critical(f"Writing server failed to initialize: {e}", exc_info=True)
    finally:
         logging.info("Writing server thread finished.")

# --- Main Execution ---
if __name__ == "__main__":
    logging.info(f"Starting server on host {HOST}...")
    # Start threads
    write_thread = threading.Thread(target=writing_server, args=(HOST, WRITING_PORT), daemon=True, name="WritingThread")
    read_thread = threading.Thread(target=reading_server, args=(HOST, READING_PORT), daemon=True, name="ReadingThread")
    write_thread.start()
    read_thread.start()

    logging.info(f"Server running [Send Port: {READING_PORT}, Recv Port: {WRITING_PORT}]. Press Ctrl+C to stop.")
    try:
        # Keep main thread alive - threads are daemons so main needs to wait
        while write_thread.is_alive() and read_thread.is_alive():
            time.sleep(1) # Wait actively
    except KeyboardInterrupt:
        logging.info("Ctrl+C received. Shutting down...")
    except Exception as e:
        logging.critical(f"Server main loop error: {e}")
    finally:
        # No explicit socket closing needed here - handled by 'with' statement in threads
        # Daemon threads will exit when main thread exits
        print("Server stopped.")
        sys.exit(0)
