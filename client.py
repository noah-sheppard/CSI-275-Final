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
import sys
import logging
import time
import re

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(threadName)s] %(levelname)s - %(message)s')

def send_message(sock, message_data):
    """Packs and sends a message."""
    try:
        encoded_message = json.dumps(message_data).encode('utf-8')
        length_prefix = struct.pack('>I', len(encoded_message))
        sock.sendall(length_prefix)
        sock.sendall(encoded_message)
        return True
    except (socket.error, BrokenPipeError, OSError) as e:
        logging.error(f"Send failed: {e}")
        return False

def receive_message(sock):
    """Receives and unpacks a message."""
    try:
        length_prefix = sock.recv(4)
        if not length_prefix or len(length_prefix) < 4: return None
        message_length = struct.unpack('>I', length_prefix)[0]
        received_data = b''
        while len(received_data) < message_length:
            chunk = sock.recv(min(message_length - len(received_data), 4096))
            if not chunk: return None
            received_data += chunk
        return json.loads(received_data.decode('utf-8'))
    except (socket.error, struct.error, json.JSONDecodeError, ConnectionResetError, OSError) as e:
        logging.error(f"Receive failed: {e}")
        return None

SERVER_IP = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
READING_PORT = 65432 # Server port we SEND to
WRITING_PORT = 65433 # Server port we RECEIVE from
stop_event = threading.Event()

# --- Client Threads ---
def handle_sending(send_sock, screen_name):
    """Handles user input and sends messages."""
    logging.info("Sending thread ready. Enter messages or commands.")
    print("\nType messages, '@recipient msg', or '!exit'.")

    while not stop_event.is_set():
        try:
            user_input = input(f"{screen_name}> ") # Blocking input

            if stop_event.is_set(): break

            if not user_input.strip(): continue # Ignore empty

            msg_to_send = None
            if user_input.lower() == '!exit':
                msg_to_send = ["EXIT", screen_name]
                logging.info("Sending EXIT.")
                send_message(send_sock, msg_to_send) # Try to send exit
                stop_event.set() # Signal stop regardless of send success
                break
            elif user_input.startswith('@'):
                match = re.match(r'^@(\w+)\s+(.*)', user_input, re.DOTALL)
                if match:
                    recipient, text = match.groups()
                    msg_to_send = ["PRIVATE", screen_name, text.strip(), recipient]
                else:
                    print("Invalid private message format: @recipient message")
            else: # Default to broadcast
                msg_to_send = ["BROADCAST", screen_name, user_input.strip()]

            if msg_to_send:
                if not send_message(send_sock, msg_to_send):
                    print("\n--- Failed to send message. Server down? Exiting. ---")
                    stop_event.set()
                    break

        except (EOFError, KeyboardInterrupt):
            print("\n--- Exiting... ---")
            if not stop_event.is_set():
                send_message(send_sock, ["EXIT", screen_name]) # Try polite exit
                stop_event.set()
            break
        except Exception as e:
            logging.error(f"Sending error: {e}")
            if not stop_event.is_set(): stop_event.set()
            break
    logging.info("Sending thread finished.")

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


# --- Main Execution ---
if __name__ == "__main__":
    while True:
        s_name = input("Enter screen name (no spaces/@): ")
        if s_name and not re.search(r'\s|@', s_name): break
        else: print("Invalid name.")

    logging.info(f"Client starting as '{s_name}', connecting to {SERVER_IP}...")
    send_sock, recv_sock = None, None # Define for finally block

    try:
        send_sock = socket.create_connection((SERVER_IP, READING_PORT))
        recv_sock = socket.create_connection((SERVER_IP, WRITING_PORT))
        logging.info("Sockets connected.")

        print("Connected! Starting threads...")
        stop_event.clear()

        recv_thread = threading.Thread(target=handle_receiving, args=(recv_sock, s_name), daemon=True, name="Receiver")
        send_thread = threading.Thread(target=handle_sending, args=(send_sock, s_name), daemon=True, name="Sender")

        recv_thread.start()
        time.sleep(0.1) # Give receiver a tiny head start to send START
        send_thread.start()

        # Keep main thread alive while worker threads run or until stop event
        while not stop_event.is_set():
            time.sleep(0.5) # Check stop_event periodically

    except socket.error as e:
        print(f"\n--- Connection Error: {e} ---")
        logging.critical(f"Cannot connect to server: {e}")
    except Exception as e:
        print(f"\n--- Unexpected Error: {e} ---")
        logging.critical(f"Client main error: {e}", exc_info=True)
    finally:
        print("--- Disconnecting ---")
        stop_event.set() # Ensure threads know to stop
        # Close sockets if they exist
        if send_sock:
             try: send_sock.close()
             except OSError: pass
        if recv_sock:
             try: recv_sock.close()
             except OSError: pass
        logging.info("Client finished.")
        print("Goodbye.")
        # Give threads a moment to potentially finish cleanup
        time.sleep(0.2)
        sys.exit(0) # Force exit if threads hang
