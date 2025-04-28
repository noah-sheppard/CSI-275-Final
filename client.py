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


def handle_receiving(recv_sock, screen_name):
    """Handles receiving messages and printing them."""
    logging.info("Receiving thread started.")
    start_success = False
    try:
        # ***** LOG POINT 5 *****
        logging.info(f"***** RECEIVER({screen_name}): Attempting to send START *****")
        start_success = send_message(recv_sock, ["START", screen_name])
        # ***** LOG POINT 6 *****
        logging.info(f"***** RECEIVER({screen_name}): START message sent, success={start_success} *****")
    except Exception as e:
        # ***** LOG POINT 7 *****
        logging.error(f"***** RECEIVER({screen_name}): Exception during START send: {e} *****")
        stop_event.set() # Signal exit if START fails catastrophically
        return

    if not start_success:
        print("\n--- Failed to send START to server. Cannot join. ---")
        stop_event.set()
        return

    # ***** LOG POINT 8 *****
    logging.info(f"***** RECEIVER({screen_name}): Entering receive loop *****")
    while not stop_event.is_set():
        msg = receive_message(recv_sock)
        # ***** LOG POINT 9 *****
        # logging.debug(f"***** RECEIVER({screen_name}): Received raw msg: {msg} *****") # DEBUG if needed

        if msg is None: # Handle disconnect or receive error
            if not stop_event.is_set():
                print("\n--- Connection lost with server. Press Enter to exit. ---")
                stop_event.set() # Signal the sending thread
            break # Exit loop

        # ... (rest of message processing and printing remains the same) ...
        try:
            if not isinstance(msg, list) or not msg: continue

            msg_type = msg[0]
            display_text = None

            # ... (message type handling like BROADCAST, PRIVATE, etc) ...
            if msg_type == "BROADCAST" and len(msg) == 3:
                sender, text = msg[1], msg[2]
                if sender == "Server": display_text = f"--- {text} ---"
                elif sender != screen_name: display_text = f"{sender}: {text}"
            elif msg_type == "PRIVATE" and len(msg) == 4:
                sender, text = msg[1], msg[2]
                display_text = f"{sender} (private): {text}"
            elif msg_type == "EXIT" and len(msg) == 2:
                 sender = msg[1]
                 if sender != screen_name: display_text = f"--- {sender} has left. ---"
            elif msg_type == "START_FAIL" and len(msg) == 3:
                 reason = msg[2]
                 print(f"\n!!! SERVER REJECTED: {reason}. Exiting. !!!")
                 stop_event.set()
                 break

            if display_text:
                print(f"\n{display_text}")

        except Exception as e:
            logging.error(f"Error processing message {msg}: {e}")


    logging.info(f"***** RECEIVER({screen_name}): Exited receive loop *****")
    logging.info("Receiving thread finished.")
    print()


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
