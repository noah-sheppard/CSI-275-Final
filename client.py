# --- START OF FILE client.py ---

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

SERVER_IP = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
READING_PORT = 65432
WRITING_PORT = 65433
stop_event = threading.Event()

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(threadName)s] %(levelname)s - %(message)s')

def _close_socket_safely(sock, sock_description="socket"):
    if sock:
        try:
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
        logging.error("Send failed: %s", e)
        return False
    except Exception as e:
        logging.error("Unexpected error sending message: %s", e)
        return False

def receive_message(sock):
    """Receives and unpacks a message (socket -> length prefix -> body -> UTF-8 -> JSON -> list)."""
    try:
        length_prefix = sock.recv(4)
        if not length_prefix or len(length_prefix) < 4:
            logging.warning("Connection closed or invalid length prefix received.")
            return None
        message_length = struct.unpack('>I', length_prefix)[0]

        received_data = b''
        while len(received_data) < message_length:
            chunk_size = min(message_length - len(received_data), 4096)
            chunk = sock.recv(chunk_size)
            if not chunk:
                logging.error("Connection broken while receiving message body.")
                return None
            received_data += chunk

        return json.loads(received_data.decode('utf-8'))
    except (socket.error, struct.error, json.JSONDecodeError, ConnectionResetError, OSError) as e:
        logging.error("Receive failed: %s", e)
        return None
    except Exception as e:
        logging.error("Unexpected error receiving message: %s", e)
        return None

def _handle_user_input(user_input, screen_name):
    """Parses user input into a message list or None."""
    msg_to_send = None
    if user_input.lower() == '!exit':
        msg_to_send = ["EXIT", screen_name]
        logging.info("Sending EXIT command to server.")
    elif user_input.startswith('@'):
        match = re.match(r'^@(\w+)\s+(.*)', user_input, re.DOTALL)
        if match:
            recipient, text = match.groups()
            if recipient and text.strip():
                 msg_to_send = ["PRIVATE", screen_name, text.strip(), recipient]
                 logging.info("Preparing private message to %s", recipient)
            else:
                 print("Invalid private message format: Use @recipient message")
        else:
            print("Invalid private message format: Use @recipient message")
    else:
        msg_to_send = ["BROADCAST", screen_name, user_input.strip()]
        logging.info("Preparing broadcast message")
    return msg_to_send

def handle_sending(sending_socket, screen_name):
    """Handles user input and sends messages (BROADCAST, PRIVATE, EXIT)."""
    logging.info("Sending thread ready. Enter messages or commands.")
    print("\nEnter: <message>, '@<recipient> <message>', or '!exit'.")

    while not stop_event.is_set():
        try:
            user_input = input(f"{screen_name}> ")

            if stop_event.is_set():
                break

            if not user_input.strip():
                continue

            msg_to_send = _handle_user_input(user_input, screen_name)

            if msg_to_send:
                if not send_message(sending_socket, msg_to_send):
                    print("\n--- Failed to send message. Server may be down. Exiting. ---")
                    logging.error("Send failed, assuming server is down.")
                    stop_event.set()
                    break
                if msg_to_send[0] == "EXIT":
                    stop_event.set()
                    break

        except (EOFError, KeyboardInterrupt):
            print("\n--- Input interrupted. Sending EXIT command... ---")
            if not stop_event.is_set():
                logging.info("EOF or KeyboardInterrupt, sending EXIT.")
                send_message(sending_socket, ["EXIT", screen_name])
                stop_event.set()
            break
        except Exception as e:
            logging.error("Error in sending loop: %s", e, exc_info=True)
            if not stop_event.is_set():
                stop_event.set()
            break
    logging.info("Sending thread finished.")

def _process_received_message(msg, screen_name):
    """Processes a received message list and returns text to display or None."""
    display_text = None
    try:
        if not isinstance(msg, list) or not msg:
            logging.warning("Received invalid message format: %s", msg)
            return None

        msg_type = msg[0]

        if msg_type == "BROADCAST" and len(msg) == 3:
             sender, text = msg[1], msg[2]
             if sender == "Server":
                  display_text = f"*** {text} ***"
             elif sender != screen_name:
                  display_text = f"{sender}: {text}"
        elif msg_type == "PRIVATE" and len(msg) == 4:
             sender, text = msg[1], msg[2]
             display_text = f"{sender} (private): {text}"
        elif msg_type == "EXIT" and len(msg) == 2:
              sender = msg[1]
              if sender != screen_name:
                   display_text = f"*** {sender} has left the chat. ***"
        elif msg_type == "START_FAIL" and len(msg) == 3:
               reason = msg[2]
               print(f"\n--- SERVER REJECTED CONNECTION: {reason}. Exiting. ---")
               logging.error("Server rejected connection: %s", reason)
               stop_event.set()
               return "EXIT_IMMEDIATELY"

        return display_text

    except Exception as e:
        logging.error("Error processing received message %s: %s", msg, e, exc_info=True)
        return None


def handle_receiving(receiving_socket, screen_name):
    """Handles receiving messages from the server and displaying them."""
    logging.info("Receiving thread started.")
    start_success = False
    try:
        logging.info("RECEIVER(%s): Attempting to send START to writing port", screen_name)
        start_message = ["START", screen_name]
        start_success = send_message(receiving_socket, start_message)
        logging.info("RECEIVER(%s): START message sent, success=%s", screen_name, start_success)
    except Exception as e:
        logging.error("RECEIVER(%s): Exception during START send: %s", screen_name, e, exc_info=True)
        print("\n--- Error connecting to server during startup. Exiting. ---")
        stop_event.set()
        return

    if not start_success:
        print("\n--- Failed to send START to server's writing port. Cannot join. Exiting. ---")
        logging.error("Failed to send START, stopping client.")
        stop_event.set()
        return

    logging.info("RECEIVER(%s): Entering receive loop", screen_name)
    while not stop_event.is_set():
        msg = receive_message(receiving_socket)

        if msg is None:
            if not stop_event.is_set():
                print("\n--- Connection lost with server. Press Enter to exit. ---")
                logging.warning("Connection lost.")
                stop_event.set()
            break

        display_text = _process_received_message(msg, screen_name)

        if display_text == "EXIT_IMMEDIATELY":
            break
        if display_text:
             print(f"\n{display_text}")
             print(f"{screen_name}> ", end='', flush=True)


    logging.info("RECEIVER(%s): Exited receive loop", screen_name)


def get_valid_screen_name():
    """Prompts user for screen name until a valid one is entered."""
    while True:
        s_name = input("Enter screen name (letters/numbers, no spaces/@): ")
        if s_name and re.match(r'^\w+$', s_name) and '@' not in s_name:
            return s_name
        print("Invalid screen name. Please use only letters, numbers, and underscores. No spaces or '@'.")


# --- Main Execution ---
if __name__ == "__main__":
    screen_name_main = get_valid_screen_name()

    logging.info("Client starting as '%s', connecting to server at %s...", screen_name_main, SERVER_IP)
    main_send_sock = None
    main_recv_sock = None
    recv_thread = None
    send_thread = None

    try:
        logging.info("Connecting sending socket to %s:%s", SERVER_IP, READING_PORT)
        main_send_sock = socket.create_connection((SERVER_IP, READING_PORT))
        logging.info("Sending socket connected.")

        logging.info("Connecting receiving socket to %s:%s", SERVER_IP, WRITING_PORT)
        main_recv_sock = socket.create_connection((SERVER_IP, WRITING_PORT))
        logging.info("Receiving socket connected.")

        print("--- Connected to server! Starting chat session... ---")
        stop_event.clear()

        recv_thread = threading.Thread(target=handle_receiving,
                                       args=(main_recv_sock, screen_name_main),
                                       daemon=True, name="ReceiverThread")
        recv_thread.start()

        time.sleep(0.2)

        send_thread = threading.Thread(target=handle_sending,
                                       args=(main_send_sock, screen_name_main),
                                       daemon=True, name="SenderThread")
        send_thread.start()

        while not stop_event.is_set():
            if (recv_thread and not recv_thread.is_alive()) or \
               (send_thread and not send_thread.is_alive()):
                 if not stop_event.is_set():
                      logging.warning("A worker thread terminated unexpectedly. Signaling stop.")
                      stop_event.set()
            time.sleep(0.5)

    except socket.error as e:
        print(f"\n--- Connection Error: Could not connect to the server ({e}). ---")
        logging.critical("Cannot connect to server at %s: %s", SERVER_IP, e)
    except Exception as e:
        print(f"\n--- An unexpected error occurred: {e} ---")
        logging.critical("Client main execution error: %s", e, exc_info=True)
    finally:
        print("--- Disconnecting... ---")
        logging.info("Initiating shutdown sequence.")
        stop_event.set()

        _close_socket_safely(main_send_sock, "sending socket")
        _close_socket_safely(main_recv_sock, "receiving socket")

        logging.info("Client finished.")
        print("--- Goodbye. ---")
