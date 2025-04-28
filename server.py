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

# logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(threadName)s] %(levelname)s - %(message)s')

# --- Protocol Helpers (Essential) ---
def send_message(sock, message_data):
    """Packs and sends a message (list -> JSON -> UTF-8 -> length prefix -> socket)."""
    try:
        encoded_message = json.dumps(message_data).encode('utf-8')
        length_prefix = struct.pack('>I', len(encoded_message))
        sock.sendall(length_prefix)
        sock.sendall(encoded_message)
        return True
    except (socket.error, BrokenPipeError, OSError) as e:
        # Log minimally on send error
        logging.error(f"Send failed: {e}")
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
            if not chunk: return None # Connection closed
            received_data += chunk

        return json.loads(received_data.decode('utf-8'))
    except (socket.error, struct.error, json.JSONDecodeError, ConnectionResetError, OSError) as e:
        logging.error(f"Receive failed: {e}")
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
    logging.info(f"Broadcasting from {sender_name}: {message_list}")
    disconnected_clients = []
    with clients_lock:
        client_items = list(clients.items()) # Iterate over a copy
        for name, sock in client_items:
            if not send_message(sock, message_list):
                disconnected_clients.append(name) # Mark for removal

    # Remove disconnected outside lock iteration
    for name in disconnected_clients:
        remove_client(name, notify=False) # Don't double-notify exit

def send_private(message_list, recipient_name):
    """Sends message to a single client."""
    with clients_lock:
        sock = clients.get(recipient_name)
        if sock:
            if not send_message(sock, message_list):
                logging.warning(f"Failed sending private to {recipient_name}, removing.")
                remove_client(recipient_name, notify=True) # Notify others they left
                return False # Send failed
            return True # Send succeeded
        else:
            logging.warning(f"Private message recipient '{recipient_name}' not found.")
            return False # Recipient not found

def remove_client(screen_name, notify=True):
    """Removes client socket and optionally notifies others."""
    sock_to_close = None
    with clients_lock:
        if screen_name in clients:
            sock_to_close = clients.pop(screen_name)
            logging.info(f"Removed client '{screen_name}'")

    if sock_to_close:
        try:
            sock_to_close.close()
        except OSError as e:
            logging.error(f"Error closing socket for {screen_name}: {e}")
        if notify:
            broadcast(["EXIT", screen_name], "Server")

def handle_client_messages(send_sock, address):
    """Thread target for handling messages FROM a single client's sending socket."""
    logging.info(f"Handler started for {address}")
    client_screen_name = None
    last_received_msg = None # Store last msg for finally block context
    try:
        while True:
            msg = receive_message(send_sock)
            last_received_msg = msg # Update last received message

            if msg is None:
                logging.info(f"Connection closed by {client_screen_name or address}.")
                break # Client disconnected or error

            # ***** LOG RECEIVED MESSAGE BEFORE PROCESSING *****
            logging.info(f"***** HANDLER({client_screen_name or address}): Received raw message: {msg} *****")

            # Basic validation and name identification
            if not isinstance(msg, list) or len(msg) < 2:
                logging.warning(f"Invalid message format (not list or < 2 elements) from {client_screen_name or address}: {msg}")
                continue # Ignore malformed messages

            # Extract type and potential sender name
            msg_type = msg[0]
            sender = msg[1] # Assume sender is always second element for START/BROADCAST/PRIVATE/EXIT

            # Identify client on first valid message (check against registered clients)
            if client_screen_name is None:
                if isinstance(sender, str) and sender: # Ensure sender is valid string
                     with clients_lock:
                         # Check if this name is actually registered via the writing thread
                         if sender in clients:
                             client_screen_name = sender
                             logging.info(f"Identified {address} as '{client_screen_name}'")
                         else:
                             # This could happen if START hasn't fully processed or name mismatch
                             logging.warning(f"Sender '{sender}' from {address} not registered. Ignoring message: {msg}")
                             continue # Skip processing this message until registered
                else:
                     logging.warning(f"Invalid sender format in first message from {address}: {msg}")
                     continue # Ignore

            # Skip processing if still unidentified (shouldn't happen if logic above is correct)
            if not client_screen_name:
                logging.error(f"Logic error: client_screen_name is None for {address} despite checks.")
                continue

            # Ensure incoming message sender matches identified name (sanity check)
            if sender != client_screen_name:
                 logging.warning(f"Message sender name '{sender}' does not match identified client '{client_screen_name}' from {address}. Ignoring message: {msg}")
                 continue

            # --- Process message types ---
            if msg_type == "BROADCAST" and len(msg) == 3:
                logging.info(f"Processing BROADCAST from {client_screen_name}: {msg}")
                broadcast(msg, client_screen_name)
            elif msg_type == "PRIVATE" and len(msg) == 4:
                recipient = msg[3]
                logging.info(f"Processing PRIVATE from {client_screen_name} to {recipient}: {msg}")
                send_private(msg, recipient)
            elif msg_type == "EXIT" and len(msg) == 2:
                 logging.info(f"Processing EXIT from {client_screen_name}: {msg}")
                 break # Exit loop, finally block handles cleanup
            else:
                # This catches invalid formats (wrong length) for known types or unknown types
                logging.warning(f"Unknown message type '{msg_type}' or invalid format from {client_screen_name}. Message: {msg}")

    except Exception as e:
        logging.error(f"Exception in handler for {client_screen_name or address}: {e}", exc_info=True)
    finally:
        logging.info(f"Handler stopping for {client_screen_name or address}.")
        if send_sock:
             try: send_sock.close()
             except OSError: pass
        if client_screen_name:
            # Notify if exit wasn't via EXIT message received in the loop
            was_exit_cmd = (last_received_msg and isinstance(last_received_msg, list) and
                            len(last_received_msg) == 2 and last_received_msg[0] == "EXIT")
            remove_client(client_screen_name, notify=not was_exit_cmd)
        elif address: # Log if handler exits before identifying client
             logging.warning(f"Handler for unidentified address {address} exiting.")

def reading_server(host, port):
    """Listens for client SENDING sockets and starts handler threads."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listen_sock:
        listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_sock.bind((host, port))
        listen_sock.listen()
        logging.info(f"Reading server listening on {host}:{port}")
        while True:
            try:
                client_sock, address = listen_sock.accept()
                logging.info(f"Accepted sending connection from {address}")
                thread = threading.Thread(target=handle_client_messages, args=(client_sock, address), daemon=True)
                thread.start()
            except OSError:
                logging.info("Reading server socket closed.")
                break # Stop listening if socket closed
            except Exception as e:
                logging.error(f"Error accepting connection: {e}")

def writing_server(host, port):
    """Listens for client RECEIVING sockets, handles START, registers clients."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listen_sock:
        listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_sock.bind((host, port))
        listen_sock.listen()
        logging.info(f"Writing server listening on {host}:{port}")
        while True:
            recv_sock = None # Define for potential finally block use
            address = None
            try:
                # ***** LOG POINT 1 *****
                logging.info(f"***** WRITING: Waiting to accept a connection on {port}... *****")
                recv_sock, address = listen_sock.accept()
                # ***** LOG POINT 2 *****
                logging.info(f"***** WRITING: Accepted receiving connection from {address} *****")

                # ***** LOG POINT 3 *****
                logging.info(f"***** WRITING: Attempting to receive START from {address} *****")
                msg = receive_message(recv_sock) # This call might block or fail
                # ***** LOG POINT 4 *****
                logging.info(f"***** WRITING: Received from {address}: {msg} *****")

                # Check if msg is valid before processing
                if msg and isinstance(msg, list) and len(msg) >= 1 and msg[0] == "START":
                    # Check screen_name validity within the START message
                    if len(msg) == 2 and isinstance(msg[1], str) and msg[1]:
                         screen_name_to_add = msg[1] # Use a distinct variable name
                         client_added = False # Flag to track if registration occurred

                         logging.info(f"***** WRITING: Received valid START for '{screen_name_to_add}' from {address} *****")
                         # --- Lock acquired here ---
                         with clients_lock:
                             if screen_name_to_add in clients:
                                 logging.warning(f"Screen name '{screen_name_to_add}' taken. Rejecting {address}.")
                                 # Send failure message while holding lock is okay, but keep it brief
                                 send_message(recv_sock, ["START_FAIL", "Server", "Screen name taken."])
                                 # Close socket *after* releasing lock if possible, or handle potential errors if closed under lock
                             else:
                                 # Add the client under the lock
                                 clients[screen_name_to_add] = recv_sock
                                 logging.info(f"Registered '{screen_name_to_add}' from {address} under lock.")
                                 client_added = True # Mark that we added the client within the lock
                         # --- Lock released here ---

                         # ***** MODIFICATION: Broadcast *after* releasing the lock *****
                         if client_added:
                             # Perform broadcast I/O outside the critical section
                             logging.info(f"Broadcasting join for '{screen_name_to_add}' after releasing lock.")
                             broadcast(["BROADCAST", "Server", f"{screen_name_to_add} has joined!"])
                         elif recv_sock: # If client wasn't added (name taken), close the socket now
                             try:
                                 recv_sock.close()
                                 logging.info(f"Closed rejected connection from {address}.")
                             except OSError as e:
                                  logging.error(f"Error closing rejected socket from {address}: {e}")

                    else:
                         # Handle invalid START format (e.g., missing/invalid name)
                         logging.warning(f"Invalid START format from {address}. Received: {msg}. Closing.")
                         if recv_sock:
                             try: recv_sock.close()
                             except OSError: pass
                else:
                    # Handle non-START messages or receive failures (msg is None)
                    logging.warning(f"Did not receive valid START from {address}. Received: {msg}. Closing.")
                    if recv_sock:
                        try: recv_sock.close()
                        except OSError: pass

            except OSError as e:
                # Errors accepting connections or major socket issues
                logging.info(f"Writing server socket closed or error accepting: {e}.")
                break # Stop listening if listen socket has issues
            except Exception as e:
                # Catch other exceptions during processing for a specific connection
                logging.error(f"Error in writing_server loop processing connection {address or 'N/A'}: {e}", exc_info=True)
                # Attempt to close the specific problematic client socket
                if recv_sock:
                    try:
                        recv_sock.close()
                    except OSError:
                        pass # Ignore errors if already closed

        # End of while loop
        logging.info("Writing server thread finished.")

# --- Main Execution ---
if __name__ == "__main__":
    logging.info("Starting server...")
    threading.Thread(target=writing_server, args=(HOST, WRITING_PORT), daemon=True, name="WritingThread").start()
    threading.Thread(target=reading_server, args=(HOST, READING_PORT), daemon=True, name="ReadingThread").start()
    logging.info(f"Server running (R:{READING_PORT}, W:{WRITING_PORT}). Press Ctrl+C to stop.")
    try:
        while True: time.sleep(3600) # Keep main thread alive
    except KeyboardInterrupt:
        logging.info("Shutting down server...")
    except Exception as e:
        logging.critical(f"Server main loop error: {e}")
    finally:
        print("Server stopped.")
        sys.exit(0)
