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

HOST = '0.0.0.0' # Listen on all available network interfaces
READING_PORT = 65432 # Port for clients to SEND messages TO
WRITING_PORT = 65433 # Port for clients to RECEIVE messages FROM
clients = {} # Dictionary to store {screen_name: receiving_socket} pairs
clients_lock = threading.Lock() # Lock for thread-safe access to the clients dictionary

# Logging setup
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(threadName)s] %(levelname)s: %(message)s',
                    datefmt='%H:%M:%S')

def _close_socket_safely(sock, sock_description="socket"):
    """Closes a socket safely, logging errors."""
    if sock:
        try:
            # sock.shutdown(socket.SHUT_RDWR) # Causes issues if already closed
            sock.close()
            logging.info("%s closed.", sock_description.capitalize())
        except OSError as e:
            # Log the specific error `e`
            logging.warning("Error closing %s: %s", sock_description, e)

def send_message(sock, message_data):
    """Packs and sends a message (list -> JSON -> UTF-8 -> length prefix -> socket)."""
    try:
        encoded_message = json.dumps(message_data).encode('utf-8')
        # Prefix message with its length (4-byte unsigned integer, network byte order)
        length_prefix = struct.pack('>I', len(encoded_message))
        sock.sendall(length_prefix)
        sock.sendall(encoded_message)
        return True
    except (socket.error, BrokenPipeError, OSError) as e:
        # Log less verbosely during normal operation like broadcasts
        # Log socket details if possible and helpful
        peer = "N/A"
        try:
            peer = sock.getpeername()
        except OSError:
            pass # Socket might already be closed/invalid
        # Log at debug level for potentially frequent errors during broadcast
        logging.debug("Send failed for %s: %s", peer, e)
        return False
    except Exception as e: # Catch other unexpected send errors
        logging.error("Unexpected error sending message: %s", e, exc_info=True)
        return False

def receive_message(sock):
    """Receives and unpacks a message (socket -> length prefix -> body -> UTF-8 -> JSON -> list)."""
    try:
        # Read the 4-byte length prefix
        length_prefix = sock.recv(4)
        if not length_prefix or len(length_prefix) < 4:
            # Log level info, as this is often a normal disconnect
            # Changed to debug to reduce noise on normal disconnects
            logging.debug("Receive returned no length prefix, connection likely closed.")
            return None
        message_length = struct.unpack('>I', length_prefix)[0]

        # Basic sanity check for message length (optional, prevents huge allocations)
        # if message_length > 10 * 1024 * 1024: # e.g., 10MB limit
        #     logging.error("Received excessive message length: %d", message_length)
        #     return None

        # Read the exact message length
        received_data = b''
        while len(received_data) < message_length:
            chunk_size = min(message_length - len(received_data), 4096)
            chunk = sock.recv(chunk_size)
            if not chunk:
                logging.warning("Connection broken while receiving message body.")
                return None
            received_data += chunk

        # Decode and parse the JSON message
        return json.loads(received_data.decode('utf-8'))
    except (socket.error, struct.error, json.JSONDecodeError,
            ConnectionResetError, OSError) as e:
        if isinstance(e, ConnectionResetError):
            # Log info level for expected disconnect types
            logging.info("Connection reset by peer.")
        else:
            # Log warning for other receive errors
            logging.warning("Receive failed: %s", e)
        return None
    except Exception as e: # Catch other unexpected receive errors
        logging.error("Unexpected error receiving message: %s", e, exc_info=True)
        return None

def broadcast(message_list, sender_name="Server"):
    """Sends a message to all currently connected clients."""
    msg_type = message_list[0]
    num_clients = 0
    disconnected_clients = []

    # Get client count under lock
    with clients_lock:
        num_clients = len(clients)

    # Prepare log message outside lock
    log_msg_tmpl = ""
    log_args = []
    if msg_type == "BROADCAST":
        log_text = message_list[2] if len(message_list) > 2 else "(Join/Leave message)"
        # Break log format string for length
        log_msg_tmpl = "Broadcasting '%s' from '%s' to %d clients: '%s%s'"
        log_args = [
            msg_type, sender_name, num_clients,
            log_text[:50], '...' if len(log_text)>50 else ''
        ]
    elif msg_type == "EXIT":
        # Break log format string for length
        log_msg_tmpl = "Broadcasting '%s' notification for '%s' to %d clients."
        log_args = [msg_type, sender_name, num_clients]

    # Log intent
    if log_msg_tmpl:
        logging.info(log_msg_tmpl, *log_args)

    # Send messages under lock
    with clients_lock:
        # Iterate over a copy for safe removal within loop if needed indirectly
        client_items = list(clients.items())
        for name, sock in client_items:
            if not send_message(sock, message_list):
                # Break log string for length
                logging.warning("Send failed to '%s' during broadcast."
                                " Marking for removal.", name)
                disconnected_clients.append(name)

    # Remove disconnected clients outside the lock iteration
    for name in disconnected_clients:
        # Don't notify=True here, failure likely means they're already gone
        # Handler thread's finally block will handle notification if needed
        remove_client(name, notify=False)

def send_private(message_list, recipient_name):
    """Sends a private message to a single specific client."""
    # msg_type is unused, remove assignment
    # msg_type = message_list[0] # Unused variable W0612
    sender_name, text = message_list[1], message_list[2]

    # Break log string for length
    logging.info("Attempting PM from '%s' to '%s': '%s%s'",
                 sender_name, recipient_name, text[:50],
                 '...' if len(text)>50 else '')

    sock_to_send = None
    recipient_found = False
    # Find recipient under lock
    with clients_lock:
        if recipient_name in clients:
            sock_to_send = clients[recipient_name]
            recipient_found = True

    # Send message outside lock
    if recipient_found and sock_to_send:
        if not send_message(sock_to_send, message_list):
            # Break log string for length
            logging.warning("Send failed for PM to '%s'."
                            " Removing client.", recipient_name)
            # Notify others if PM send fails (implies recipient disconnected)
            remove_client(recipient_name, notify=True)
            return False # Indicate PM failure
        logging.info("PM successfully sent to '%s'.", recipient_name)
        return True # Indicate PM success

    if recipient_found and not sock_to_send:
        # This case should ideally not happen
        # Break log string for length
        logging.error("Logic error: Recipient '%s' found but socket was None"
                      " during PM send.", recipient_name)
        return False

    # Recipient not found
    logging.warning("PM recipient '%s' not found.", recipient_name)
    # Optionally notify sender? Not implemented here for simplicity.
    return False # Indicate recipient not found

def remove_client(screen_name, notify=True):
    """Removes client, closes socket, optionally broadcasts departure."""
    sock_to_close = None
    client_was_present = False
    # Remove from dictionary under lock
    with clients_lock:
        if screen_name in clients:
            sock_to_close = clients.pop(screen_name) # Remove and get socket
            client_was_present = True
            logging.info("Removing '%s' from active clients.", screen_name)

    # Close socket outside lock
    if sock_to_close:
        # Use helper to close socket
        # Break log string for length
        _close_socket_safely(sock_to_close,
                             f"receiving socket for '{screen_name}'")

        # Broadcast notification outside lock if needed
        if notify and client_was_present:
            logging.info("Broadcasting exit notification for '%s'.", screen_name)
            # Break list for length/clarity
            exit_notification = [
                "BROADCAST",
                "Server",
                f"{screen_name} has left the chat."
            ]
            broadcast(exit_notification, "Server") # Broadcast departure

# Helper function for handle_client_messages
def _identify_client(msg, address):
    """Try to identify the client screen name from a message."""
    sender = msg[1]
    client_screen_name = None
    if isinstance(sender, str) and sender:
        with clients_lock:
            if sender in clients:
                client_screen_name = sender
                # Break log string for length
                logging.info("Handler identified connection %s as '%s'",
                             address, client_screen_name)
            else:
                # Break log string for length
                logging.warning("Received message from %s with sender '%s',"
                                " but name not registered. Ignoring.", address, sender)
    else:
        # Break log string for length
        logging.warning("Received message from %s with invalid sender field: %s."
                        " Ignoring.", address, msg)
    return client_screen_name

# Helper function for handle_client_messages
def _process_client_message(msg, client_screen_name, address):
    """Process a validated message from an identified client."""
    msg_type = msg[0]
    sender = msg[1]

    # Verify sender matches identified client for this handler
    if sender != client_screen_name:
        # Break log string for length
        logging.warning("Received message from %s claiming to be '%s',"
                        " but handler associated with '%s'. Ignoring.",
                        address, sender, client_screen_name)
        return None # Explicitly return None here

    # Route message based on type
    if msg_type == "BROADCAST" and len(msg) == 3:
        broadcast(msg, client_screen_name)
    elif msg_type == "PRIVATE" and len(msg) == 4:
        recipient = msg[3]
        if not isinstance(recipient, str) or not recipient:
            # Break log string for length
            logging.warning("Received invalid PRIVATE message from '%s':"
                            " Bad recipient '%s'.", client_screen_name, recipient)
            return None # Explicitly return None here
        send_private(msg, recipient)
    elif msg_type == "EXIT" and len(msg) == 2:
        logging.info("Received EXIT command from '%s'. Closing connection.",
                     client_screen_name)
        return "EXIT" # Signal to exit loop
    else:
        # Break log string for length
        logging.warning("Unknown message type '%s' or format from '%s': %s",
                        msg_type, client_screen_name, msg)

    # Ensure explicit return for consistent return type (R1710 fix)
    return None

def handle_client_messages(handler_sock, address):
    """Thread target: Handles messages FROM a single client."""
    # Break log string for length
    logging.info("Handler started for new connection from %s."
                 " Waiting for identification.", address)
    client_screen_name = None
    last_received_msg = None

    try:
        while True:
            msg = receive_message(handler_sock)
            last_received_msg = msg

            if msg is None: # Clean disconnect or receive error
                logging.info("Connection closed by %s.",
                             client_screen_name or address)
                break # Exit loop

            # Basic message validation
            if not isinstance(msg, list) or len(msg) < 2:
                # Break log string for length
                logging.warning("Received malformed message from %s: %s",
                                client_screen_name or address, msg)
                continue # Ignore malformed

            # Identify client if not already done
            if client_screen_name is None:
                client_screen_name = _identify_client(msg, address)
                if client_screen_name is None:
                    continue # Identification failed, wait for next message

            # Process the message using helper
            action = _process_client_message(msg, client_screen_name, address)
            if action == "EXIT":
                break # Exit loop based on action

    except (socket.error, OSError) as e:
        # More specific catch for network errors
        logging.warning("Network error in handler for %s: %s",
                        client_screen_name or address, e)
    except Exception as e: # Catch unexpected errors in handler loop
        # Log with traceback
        logging.error("Exception in handler for %s: %s",
                      client_screen_name or address, e, exc_info=True)
    finally:
        logging.info("Handler stopping for %s.",
                     client_screen_name or address)
        # Use helper to close socket
        # Break log string for length
        _close_socket_safely(handler_sock,
                             f"sending socket for {client_screen_name or address}")

        # Clean up client registration if identified
        if client_screen_name:
            # Determine if loop exited due to clean EXIT command
            was_exit_cmd = (isinstance(last_received_msg, list) and
                            len(last_received_msg) == 2 and
                            last_received_msg[0] == "EXIT" and
                            last_received_msg[1] == client_screen_name)
            # Notify others unless clean EXIT command caused stop
            remove_client(client_screen_name, notify=not was_exit_cmd)


def reading_server(host, port):
    """Main thread target: Listens for new client connections on the READING_PORT."""
    listen_sock = None
    try:
        listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_sock.bind((host, port))
        listen_sock.listen()
        # Break log string for length
        logging.info("Reading server listening on %s:%s"
                     " for client sending sockets", host, port)

        while True: # Loop to accept connections
            try:
                client_sock, address = listen_sock.accept()
                logging.info("Accepted SENDING connection from %s", address)

                # Create and start handler thread for the new connection
                thread = threading.Thread(
                    target=handle_client_messages,
                    args=(client_sock, address), # Pass the new client socket
                    daemon=True, # Allow main thread to exit even if handlers run
                    name=f"Handler-{address[1]}" # Concise thread name
                )
                thread.start()
                logging.info("Started handler thread for %s", address)

            except OSError as e:
                # Break log string for length
                logging.info("Reading server socket closed (%s)."
                             " Stopping accept loop.", e)
                break # Exit loop if listening socket closed
            except Exception as e: # Catch error during accept/thread start
                logging.error("Error accepting connection"
                              " in reading thread: %s", e, exc_info=True)
                time.sleep(1) # Avoid busy-looping on accept errors
    except Exception as e: # Catch error during server initialization
        # Log with traceback
        # Break log string for length
        logging.critical("Reading server failed to initialize on %s:%s: %s",
                         host, port, e, exc_info=True)
    finally:
        # Use helper to close socket
        _close_socket_safely(listen_sock, "Reading server listening socket")
        logging.info("Reading server thread finished.")

# Helper function for writing_server
def _validate_start_message(msg):
    """Checks if a message is a valid START message."""
    # Break conditions for length/readability and clarity
    if not isinstance(msg, list):
        return False

    if len(msg) != 2:
        return False

    if msg[0] != "START":
        return False

    # Check name is non-empty string
    if not isinstance(msg[1], str) or not msg[1]:
        return False

    return True # All checks passed


# Helper function for writing_server
def _handle_registration(registration_sock, address):
    """Handles the START message and client registration logic."""
    msg = receive_message(registration_sock)
    client_added = False
    screen_name_to_add = None

    # Validate the received START message
    if _validate_start_message(msg):
        screen_name_to_add = msg[1]
        # Check name availability and add under lock
        with clients_lock:
            if screen_name_to_add in clients:
                # Name taken - reject connection
                # Break log string for length
                logging.warning("Client registration rejected: '%s' from %s"
                                " (Name taken)", screen_name_to_add, address)
                # Send failure message back to client
                # Break list for length/clarity
                fail_msg = ["START_FAIL", "Server",
                            "Screen name is already taken."]
                send_message(registration_sock, fail_msg)
                # Use helper to close socket (it's rejected)
                # Break log string for length
                _close_socket_safely(registration_sock,
                                     f"rejected registration socket from {address}")
            else:
                # Name available - register client
                clients[screen_name_to_add] = registration_sock # Store the receiving socket
                # Break log string for length
                logging.info("Client registered successfully: '%s' from %s",
                             screen_name_to_add, address)
                client_added = True
                # Keep registration_sock open for sending messages TO this client

        # Broadcast arrival *after* releasing lock, only if added
        if client_added:
            # Break list for length/clarity
            join_notification = [
                "BROADCAST",
                "Server",
                f"{screen_name_to_add} has joined the chat!"
            ]
            broadcast(join_notification, "Server")
    else:
        # Invalid START message received
        # Break log string for length
        logging.warning("Invalid START message received from %s."
                        " Closing connection. Message: %s", address, msg)
        # Use helper to close socket (invalid registration attempt)
        # Break log string for length
        _close_socket_safely(registration_sock,
                             f"invalid START socket from {address}")


def writing_server(host, port):
    """Main thread: Listens on WRITING_PORT, handles START msg & registration."""
    listen_sock = None
    try:
        listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_sock.bind((host, port))
        listen_sock.listen()
        # Break log string for length
        logging.info("Writing server listening on %s:%s"
                     " for client receiving sockets", host, port)

        while True: # Loop to accept registration connections
            recv_sock = None # Socket for the current connection attempt
            address = None
            try:
                recv_sock, address = listen_sock.accept()
                # Break log string for length
                logging.info("Accepted RECEIVING connection from %s."
                             " Awaiting START.", address)
                # Handle registration logic in a separate function
                _handle_registration(recv_sock, address)

            except OSError as e:
                # Break log string for length
                logging.info("Writing server socket closed (%s)."
                             " Stopping accept loop.", e)
                break # Exit accept loop if listening socket closed
            except Exception as e: # Catch error during registration handling
                # Log with traceback
                # Break log string for length
                logging.error("Error handling registration connection from %s"
                              " in writing thread: %s", address or 'N/A', e,
                              exc_info=True)
                # Use helper to close the specific socket that failed
                # Break log string for length
                _close_socket_safely(recv_sock,
                                     f"failed registration socket from {address or 'N/A'}")
                time.sleep(1) # Avoid busy-looping on persistent errors

    except Exception as e: # Catch error during server initialization
        # Log with traceback
        # Break log string for length
        logging.critical("Writing server failed to initialize on %s:%s: %s",
                         host, port, e, exc_info=True)
    finally:
        # Use helper to close socket
        _close_socket_safely(listen_sock, "Writing server listening socket")
        # Break log string for length
        logging.info("Writing server shutting down."
                     " Cleaning up remaining client sockets.")
        # Clean up client sockets under lock
        with clients_lock:
            # Make a copy to avoid modification during iteration issues
            client_items = list(clients.items())
            for name, sock in client_items:
                logging.info("Closing socket for '%s' during shutdown.", name)
                # Use helper to close socket
                _close_socket_safely(sock, f"socket for {name}")
            clients.clear() # Clear the dictionary after closing all sockets
        logging.info("Writing server thread finished.")


# --- Main Execution ---
if __name__ == "__main__":
    logging.info("Starting chat server on host: %s", HOST)

    # Create server threads
    write_thread = threading.Thread(target=writing_server,
                                    args=(HOST, WRITING_PORT),
                                    daemon=True, name="WritingThread")
    read_thread = threading.Thread(target=reading_server,
                                   args=(HOST, READING_PORT),
                                   daemon=True, name="ReadingThread")

    # Start threads
    write_thread.start()
    read_thread.start()

    # Break log string for length
    logging.info("Server running [Send-to Port: %s, Receive-from Port: %s]."
                 " Press Ctrl+C to stop.", READING_PORT, WRITING_PORT)

    try:
        # Keep main thread alive while server threads run
        while write_thread.is_alive() and read_thread.is_alive():
            time.sleep(1) # Periodically check thread status
    except KeyboardInterrupt:
        logging.info("Ctrl+C received. Initiating server shutdown...")
        # Rely on finally block and daemon threads for cleanup
    except Exception as e: # Catch unexpected errors in main loop
        # Log with traceback
        logging.critical("Server main loop encountered an unexpected error: %s",
                         e, exc_info=True)
    finally:
        # Threads are daemons, main thread exit will trigger their shutdown.
        # The finally blocks in the thread targets handle socket cleanup.
        logging.info("Server shutdown sequence complete.")
        print("Server stopped.")
        sys.exit(0)
