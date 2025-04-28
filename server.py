import socket
import threading
import json
import struct
import logging
import sys
import time

# Basic logging setup
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
    try:
        while True:
            msg = receive_message(send_sock)
            if msg is None: break # Client disconnected or error

            # Basic validation and name identification
            if not isinstance(msg, list) or len(msg) < 2: continue
            msg_type, sender = msg[0], msg[1]

            # Identify client on first valid message
            if client_screen_name is None:
                with clients_lock:
                    if sender in clients:
                        client_screen_name = sender
                        logging.info(f"Identified {address} as '{client_screen_name}'")
                    else:
                        logging.warning(f"Sender '{sender}' from {address} not registered. Ignoring.")
                        continue # Wait for registration via writing thread

            if not client_screen_name: continue # Skip if still unidentified

            # Process message types
            if msg_type == "BROADCAST" and len(msg) == 3:
                broadcast(msg, client_screen_name)
            elif msg_type == "PRIVATE" and len(msg) == 4:
                recipient = msg[3]
                send_private(msg, recipient) # We don't notify sender on failure here
            elif msg_type == "EXIT":
                logging.info(f"'{client_screen_name}' sent EXIT.")
                break # Exit loop, finally block handles cleanup
            else:
                logging.warning(f"Unknown/invalid message from {client_screen_name}: {msg}")

    except Exception as e:
        logging.error(f"Error in handler for {client_screen_name or address}: {e}", exc_info=True)
    finally:
        logging.info(f"Handler stopping for {client_screen_name or address}.")
        if send_sock:
             try: send_sock.close()
             except OSError: pass
        if client_screen_name:
            # Notify if exit wasn't via EXIT message
            was_exit_cmd = (msg and msg[0] == "EXIT") if 'msg' in locals() else False
            remove_client(client_screen_name, notify=not was_exit_cmd)

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
            try:
                recv_sock, address = listen_sock.accept()
                logging.info(f"Accepted receiving connection from {address}")
                msg = receive_message(recv_sock)

                if msg and msg[0] == "START" and len(msg) == 2:
                    screen_name = msg[1]
                    with clients_lock:
                        if screen_name in clients:
                            logging.warning(f"Screen name '{screen_name}' taken. Rejecting {address}.")
                            send_message(recv_sock, ["START_FAIL", "Server", "Screen name taken."])
                            recv_sock.close()
                        else:
                            clients[screen_name] = recv_sock
                            logging.info(f"Registered '{screen_name}' from {address}")
                            broadcast(["BROADCAST", "Server", f"{screen_name} has joined!"])
                else:
                    logging.warning(f"Invalid START from {address}. Closing.")
                    if recv_sock: recv_sock.close()

            except OSError:
                logging.info("Writing server socket closed.")
                break # Stop listening if socket closed
            except Exception as e:
                logging.error(f"Error accepting/processing START: {e}")
                if recv_sock:
                    try: recv_sock.close()
                    except OSError: pass

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