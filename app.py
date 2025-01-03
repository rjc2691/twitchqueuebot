import irc.bot
import logging
import threading
import yaml
import os
import sys
from fuzzywuzzy import process

# Enable detailed logging for the bot
logging.basicConfig(level=logging.DEBUG)

# Load the configuration from the YAML file
if getattr(sys, 'frozen', False):
    # The app is running as a bundled executable
    # Although I haven't actaully bundled it yet
    config_path = os.path.join(sys._MEIPASS, 'config.yaml')
else:
    # The app is running as a Python script
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')

with open(config_path, 'r') as file:
    config = yaml.safe_load(file)

# Access the values from the config file
twitch_user = config.get('TWITCH_USER')
oauth_token = config.get('OAUTH_TOKEN')
channel = config.get('CHANNEL')
responses = config.get('responses')
default_time = config.get('default_queue_timer_mins')*60
reminder_time = config.get('queue_reminder_timer_mins')*60
min_messages_for_reminder = config.get('min_messages_for_reminder')
operators = config.get('operators').split()

class QueueBot(irc.bot.SingleServerIRCBot):
    def __init__(self, server, port, username, oauth_token, channel):
        logging.debug(f"Initializing bot with username: {username}, channel: {channel}")
        
        # Override the parent class to set server and port
        self.server = server
        self.port = port
        self.username = username
        self.oauth_token = oauth_token
        self.channel = channel
        
        # Initialize the parent class with an empty list to prevent automatic login
        irc.bot.SingleServerIRCBot.__init__(self, [(self.server, self.port, self.oauth_token)], self.username, self.oauth_token)
        
        # Initialize an empty list to hold the queue of users
        self.queue = []
        self.queue_open = False
        self.num_of_messages_since_last_reminder = 0 # use this to avoid sending open queue reminders when no is chatting 
    
    def on_connect(self, c, e):
        logging.debug("Bot connected successfully to the server.")
        
        # Manually send the PASS (OAuth token) and NICK commands
        c.send(f"PASS {self.oauth_token}\r\n".encode('utf-8'))  # Send OAuth token first
        c.send(f"NICK {self.username}\r\n".encode('utf-8'))     # Send username
        c.send(f"USER {self.username} 0 * :{self.username}\r\n".encode('utf-8'))  # User command
        
    def on_welcome(self, c, e):
        logging.debug("Received the welcome message from the server.")
        
        # Join the channel after receiving the welcome message
        c.join(self.channel)
    
    # This function handles each message that is written in chat, and checks for queue commands
    def on_pubmsg(self, c, e):
        message = e.arguments[0]
        sender = "@" + e.source.nick
        logging.debug(f"Received message: {message}")
        # keep track of how many messages have been sent in chat for our open queue reminder
        self.num_of_messages_since_last_reminder += 1 

        # Queue Information
        if message.lower() == responses['q_resp_1']['command']:
            c.privmsg(self.channel, responses['q_resp_1']['response_value'])


        # Open the Queue
        elif message.lower() == responses['q_open_1']['command']:
            # Only if a mod uses this command
            if sender in operators:
                self.queue_open = True
                c.privmsg(self.channel, responses['q_open_1']['response_value'])
                self.start_reminder_timer(c)
        

        # Close the Queue
        elif message.lower() == responses['q_close_1']['command']:
            # Only if a mod uses this command
            if sender in operators:
                self.queue_open = False
                self.queue = []
                c.privmsg(self.channel, responses['q_close_1']['response_value'])
        

        # All commands after this one will require the queue to be open
        elif message.lower().startswith(responses['q_resp_2']['command']) and self.queue_open == False:
                c.privmsg(self.channel, responses['q_resp_2']['response_value'])
        

         # Let user join the queue
        elif message.lower() == responses['q_join_1']['command']:
            if sender not in self.queue:
                self.queue.append(sender)
                c.privmsg(self.channel, responses['q_join_1']['response_value'].format(sender=sender))
            else:
                c.privmsg(self.channel, responses['q_join_2']['response_value'].format(sender=sender))


        # Show users in the queue
        elif message.lower() == responses['q_show_1']['command']:
            # if the queue is not empty
            if self.queue:
                q_string = ', '.join(self.queue)
                c.privmsg(self.channel, responses['q_show_1']['response_value'].format(queue=q_string))
            # if the queue is empty
            else:
                c.privmsg(self.channel, responses['q_show_2']['response_value'])


        # Choose someone from the queue
        elif message.lower().startswith(responses['q_pick_1']['command']):
            # Only if a mod uses this command
            if sender in operators:
                # check if the queue is empty
                if len(self.queue) == 0:
                    c.privmsg(self.channel, responses['q_pick_4']['response_value'])
                else:

                    # Get the username passed after "pick" (if any)
                    parts = message.split()
                    
                    # Look for a time being passed for the timer
                    timer_duration = default_time
                    for s in parts:
                        if s.isdigit():
                            # remove the time from the message parts so it doesnt get confused with a username later
                            parts.remove(s) 
                            timer_duration = s

                    # Look for a username passed for the command
                    username_to_pick = ''
                    if len(parts) > 2:
                        for s in parts[2:]: # skip the first message parts so we dont confuse the command for a username
                            # use fuzzy match to pick the closest username in the queue to the text sent with the command 
                            username_to_pick = process.extractOne(s, self.queue)[0] # the returned value is tuple with a string and the levenstein distance score
                            # check the levenstein distance to make sure it's at least 70 to make sure we are fuzzy matching gibberish
                            if process.extractOne(s, self.queue)[1] < 70: # kind of an arbitrary threshold. may need to tweak
                                username_to_pick = s # if the fuzzy match was really bad, we will just keep the raw text from the command
                            
                    if len(username_to_pick) > 2: # this is basically checking if a username to pick was found or not
                        if username_to_pick in self.queue: # think checking this is redundant, but leaving for now
                            self.queue.remove(username_to_pick)
                            c.privmsg(self.channel, responses['q_pick_1']['response_value'].format(username_to_pick=username_to_pick))
                            self.start_timer(username_to_pick, timer_duration ,c)
                        else:
                            # If they are not in the queue
                            c.privmsg(self.channel, responses['q_pick_2']['response_value'].format(username_to_pick=username_to_pick))
                    else:
                        # Default behavior: Remove the first person in the queue
                        username_to_pick = self.queue.pop(0)
                        c.privmsg(self.channel, responses['q_pick_3']['response_value'].format(username_to_pick=username_to_pick))
                        self.start_timer(username_to_pick, timer_duration ,c)


        # Remove someone from the queue
        elif message.lower().startswith(responses['q_remove_1']['command']):
            # Get the username passed after "!q remove"
            parts = message.split()
            
            # Find the username to remove from the queue
            username_to_remove = ''
            if len(parts) == 2:
                username_to_remove = sender # if there isnt anything after the remove command, then they will removing themselves
            if len(parts) > 2: # just adding this to make the empty queue message display whatever the sender typed after the remove command
                username_to_remove = parts[2]

            # First check if the queue is empty
            if len(self.queue) == 0:
                c.privmsg(self.channel, responses['q_remove_2']['response_value'].format(username_to_remove=username_to_remove))
            else:
                if len(parts) > 2:
                    for s in parts[2:]: # skip the first message parts so we dont confuse the command for a username
                        if s.startswith('@'):
                            username_to_remove = s
                        elif s.isalnum():
                            # the next line does a fuzzy match incase the username wasnt typed correctly
                            username_to_remove = process.extractOne(s, self.queue)[0] # the returned value is tuple with a string and the levenstein distance score
                            # check the levenstein distance to make sure it's at least 70 to make sure we are fuzzy matching gibberish
                            if process.extractOne(s, self.queue)[1] < 70:
                                username_to_remove = '' # if the fuzzy match was really bad, we will just tell them to provide a valid name
                                c.privmsg(self.channel, responses['q_remove_3']['response_value'].format(username_to_remove=username_to_remove))
                
                username_to_remove = username_to_remove.lower()

                # Only if a mod uses this command or the user is removing themselves from the queue
                if len(username_to_remove) >2: # make sure we have a username to remove
                    if sender in operators or sender == username_to_remove:                
                        if username_to_remove in self.queue:
                            self.queue.remove(username_to_remove)
                            c.privmsg(self.channel, responses['q_remove_1']['response_value'].format(username_to_remove=username_to_remove))
                        # If they are not in the queue
                        else:
                            c.privmsg(self.channel, responses['q_remove_2']['response_value'].format(username_to_remove=username_to_remove))

    # this timer is for keeping track of how long each person in the queue gets before a new person should be picked
    def start_timer(self, removed_user, duration, c):
        duration = int(duration) * 60 # make sure the timer parameter typed in chat is converted to seconds for the timer function

        # Function to run when the timer finishes
        def time_up():
            c.privmsg(self.channel, responses['times_up']['response_value'].format(removed_user=removed_user))
        
        # Start a timer
        timer = threading.Timer(duration, time_up)
        timer.start()
    
    # this timer is for periodically reminding chat that the queue is open. Each time it reminds, it starts the next remind timer
    def start_reminder_timer(self, c):
        def remind():
            # We only remind if the queue is still open
            if self.queue_open:
                # next line makes sure we are sending reminders back to back when no one else is chatting
                if self.num_of_messages_since_last_reminder >= min_messages_for_reminder:
                    self.num_of_messages_since_last_reminder = 0 # reset the count since we are about to send a reminder
                    # Show the reminder message
                    c.privmsg(self.channel, responses['reminder']['response_value'].format(default_time=int(default_time/60)))
                    
                    # Show the current queue
                    if self.queue:
                        q_string = ', '.join(self.queue)
                        c.privmsg(self.channel, responses['q_show_1']['response_value'].format(queue=q_string))
                    # if the queue is empty
                    else:
                        c.privmsg(self.channel, responses['q_show_2']['response_value'])
                    
                #Start the next reminder timer
                timer = threading.Timer(reminder_time, remind)
                timer.start()
                
        # Start a timer
        timer = threading.Timer(reminder_time, remind)
        timer.start()
    
    def on_disconnect(self, c, e):
        logging.debug("Disconnected from the server.")
    
    def on_error(self, c, e):
        logging.error(f"Error occurred: {e}")


# Create and start the bot
bot = QueueBot('irc.chat.twitch.tv', 6667, twitch_user, oauth_token, channel)
bot.start()

