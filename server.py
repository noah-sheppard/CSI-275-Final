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

# Logging setup
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(threadName)s] %(levelname)s: %(message)s',
                    datefmt='%H:%M:%S') # Use HH:MM:SS for time

# --- Protocol Helpers (Essential) ---
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
    """Sends message to all clients using the new log format."""
    msg_type = message_list[0]
    client_count = 0
    with clients_lock: # Get count under lock for accuracy
        client_count = len(clients)

    # Log message relay intention with new format
    # Check message type to avoid logging internal EXIT broadcasts triggered by remove_client excessively
    if msg_type != "EXIT" or sender_name != "Server": # Log user broadcasts and server joins
         logging.info(f"Message: {msg_type} From: {sender_name} To: all ({client_count} clients)")

    disconnected_clients = []
    with clients_lock:
        client_items = list(clients.items()) # Iterate over a copy
        for name, sock in client_items:
            if not send_message(sock, message_list):
                # Log failures minimally
                if msg_type != "EXIT": # Don't warn about failures sending EXIT notification
                     logging.warning(f"Send failed to '{name}' during broadcast.")
                disconnected_clients.append(name) # Mark for removal

    # Remove disconnected outside lock iteration
    for name in disconnected_clients:
        remove_client(name, notify=False) # remove_client handles its own logging

def send_private(message_list, recipient_name):
    """Sends message to a single client using the new log format."""
    msg_type = message_list[0]
    sender_name = message_list[1] # Assume sender is second element

    # Log message relay intention with new format
    logging.info(f"Message: {msg_type} From: {sender_name} To: {recipient_name}")

    sock_to_send = None
    recipient_found = False
    with clients_lock:
        if recipient_name in clients:
            sock_to_send = clients[recipient_name]
            recipient_found = True

    if sock_to_send:
        if not send_message(sock_to_send, message_list):
            logging.warning(f"Send failed for PM to '{recipient_name}'. Removing client.")
            remove_client(recipient_name, notify=True) # remove_client handles logging
            return False # Send failed
        else:
            # logging.info(f"PM successfully delivered to '{recipient_name}'.") # Optional: Too verbose now
            return True # Send succeeded
    elif recipient_found:
         logging.error(f"Logic error: Recipient '{recipient_name}' found but socket was None during PM send.")
         return False
    else:
        logging.warning(f"PM recipient '{recipient_name}' not found.")
        return False # Recipient not found

def remove_client(screen_name, notify=True):
    """Removes client socket and logs the removal."""
    sock_to_close = None
    client_was_present = False
    with clients_lock:
        if screen_name in clients:
            sock_to_close = clients.pop(screen_name)
            client_was_present = True

    if sock_to_close:
        # Log the removal event clearly
        logging.info(f"Client removed: {screen_name}")
        try:
            # Shutdown may help ensure receiver knows socket is closing before actual close
            sock_to_close.shutdown(socket.SHUT_RDWR)
        except OSError: pass # Ignore if already closed/broken
        try:
            sock_to_close.close()
        except OSError: pass # Ignore closing errors for already closed socket

        # If notify is true, broadcast the EXIT message
        if notify and client_was_present:
            exit_notification = ["EXIT", screen_name]
            # broadcast() will log the EXIT message relay if needed (modified broadcast to skip server EXITs)
            broadcast(exit_notification, "Server") # Let broadcast handle sending
    # No log needed if client wasn't present


def handle_client_messages(send_sock, address):
    """Handles messages FROM a client. Logs identification and delegates message handling."""
    logging.info(f"Handler started for {address}")
    client_screen_name = None
    last_received_msg = None
    try:
        while True:
            msg = receive_message(send_sock)
            last_received_msg = msg

            if msg is None:
                logging.info(f"Connection closed by {client_screen_name or address}.")
                break

            # Basic validation
            if not isinstance(msg, list) or len(msg) < 2: continue # Ignore silently

            msg_type = msg[0]
            sender = msg[1]

            # Identify client on first valid message
            if client_screen_name is None:
                if isinstance(sender, str) and sender:
                     with clients_lock:
                         if sender in clients:
                             client_screen_name = sender
                             logging.info(f"Handler identified {address} as '{client_screen_name}'")
                         else: continue # Ignore messages until registered
                else: continue # Ignore invalid first message

            if not client_screen_name: continue # Should not happen if logic is correct

            # Verify sender matches identified client
            if sender != client_screen_name: continue # Ignore mismatched messages silently

            # --- Delegate message processing (logging happens inside broadcast/send_private) ---
            if msg_type == "BROADCAST" and len(msg) == 3:
                broadcast(msg, client_screen_name)
            elif msg_type == "PRIVATE" and len(msg) == 4:
                recipient = msg[3]
                send_private(msg, recipient)
            elif msg_type == "EXIT" and len(msg) == 2:
                 logging.info(f"Received EXIT command from '{client_screen_name}'.")
                 break # Exit loop, finally handles cleanup
            else:
                 logging.warning(f"Unknown message type '{msg_type}' or format from '{client_screen_name}'.")

    except Exception as e:
        logging.error(f"Exception in handler for {client_screen_name or address}: {e}", exc_info=False)
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


def reading_server(host, port):
    """Listens for SENDING sockets. Logs connections."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listen_sock:
            listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listen_sock.bind((host, port))
            listen_sock.listen()
            logging.info(f"Reading server listening on {host}:{port}")
            while True:
                    try:
                        client_sock, address = listen_sock.accept()
                        logging.info(f"Accepted SENDING connection from {address}")
                        thread = threading.Thread(target=handle_client_messages,
                                                  args=(client_sock, address),
                                                  daemon=True,
                                                  name=f"Handler-{address[1]}")
                        thread.start()
                    except OSError:
                         logging.info("Reading server socket closed.")
                         break
                    except Exception as e:
                         logging.error(f"Error accepting connection in reading thread: {e}")
                         time.sleep(1)
    except Exception as e:
        logging.critical(f"Reading server failed to initialize: {e}", exc_info=True)
    finally:
        logging.info("Reading server thread finished.")


def writing_server(host, port):
    """Listens for RECEIVING sockets, handles START. Logs registration."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listen_sock:
            listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listen_sock.bind((host, port))
            listen_sock.listen()
            logging.info(f"Writing server listening on {host}:{port}")
            while True:
                recv_sock = None
                address = None
                try:
                    recv_sock, address = listen_sock.accept()
                    logging.info(f"Accepted RECEIVING connection from {address}")
                    msg = receive_message(recv_sock)

                    # Validate START message
                    if msg and isinstance(msg, list) and len(msg) == 2 and \
                       msg[0] == "START" and isinstance(msg[1], str) and msg[1]:
                        screen_name_to_add = msg[1]
                        client_added = False
                        with clients_lock:
                            if screen_name_to_add in clients:
                                logging.warning(f"Client rejected: {screen_name_to_add} from {address} (Name taken)")
                                send_message(recv_sock, ["START_FAIL", "Server", "Screen name taken."])
                            else:
                                clients[screen_name_to_add] = recv_sock
                                logging.info(f"Client registered: {screen_name_to_add} from {address}")
                                client_added = True

                        # Broadcast join outside lock (broadcast logs itself)
                        if client_added:
                             broadcast(["BROADCAST", "Server", f"{screen_name_to_add} has joined!"])
                        elif recv_sock: # Close rejected socket
                             try: recv_sock.close()
                             except OSError: pass
                    else: # Invalid START
                        logging.warning(f"Invalid START from {address}. Closing connection.")
                        if recv_sock:
                            try: recv_sock.close()
                            except OSError: pass
                except OSError:
                    logging.info("Writing server socket closed.")
                    break
                except Exception as e:
                    logging.error(f"Error handling connection {address or 'N/A'} in writing thread: {e}")
                    if recv_sock:
                        try: recv_sock.close()
                        except OSError: pass
                    time.sleep(1)
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
