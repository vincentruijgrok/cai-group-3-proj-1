import sys, random, enum, ast, time, csv, json
import numpy as np
import re
from datetime import datetime, timedelta
from matrx import grid_world
from brains1.ArtificialBrain import ArtificialBrain
from actions1.CustomActions import *
from matrx import utils
from matrx.grid_world import GridWorld
from matrx.agents.agent_utils.state import State
from matrx.agents.agent_utils.navigator import Navigator
from matrx.agents.agent_utils.state_tracker import StateTracker
from matrx.actions.door_actions import OpenDoorAction
from matrx.actions.object_actions import GrabObject, DropObject, RemoveObject
from matrx.actions.move_actions import MoveNorth
from matrx.messages.message import Message
from matrx.messages.message_manager import MessageManager
from actions1.CustomActions import RemoveObjectTogether, CarryObjectTogether, DropObjectTogether, CarryObject, Drop

class Phase(enum.Enum):
    INTRO = 1,
    FIND_NEXT_GOAL = 2,
    PICK_UNSEARCHED_ROOM = 3,
    PLAN_PATH_TO_ROOM = 4,
    FOLLOW_PATH_TO_ROOM = 5,
    PLAN_ROOM_SEARCH_PATH = 6,
    FOLLOW_ROOM_SEARCH_PATH = 7,
    PLAN_PATH_TO_VICTIM = 8,
    FOLLOW_PATH_TO_VICTIM = 9,
    TAKE_VICTIM = 10,
    PLAN_PATH_TO_DROPPOINT = 11,
    FOLLOW_PATH_TO_DROPPOINT = 12,
    DROP_VICTIM = 13,
    WAIT_FOR_HUMAN = 14,
    WAIT_AT_ZONE = 15,
    FIX_ORDER_GRAB = 16,
    FIX_ORDER_DROP = 17,
    REMOVE_OBSTACLE_IF_NEEDED = 18,
    ENTER_ROOM = 19


class BaselineAgent(ArtificialBrain):
    TASKS = ["search", "clear", "rescue"]

    def __init__(self, slowdown, condition, name, folder):
        super().__init__(slowdown, condition, name, folder)
        # Initialization of some relevant variables
        self._tick = None
        self._slowdown = slowdown
        self._condition = condition
        self._human_name = name
        self._folder = folder
        self._phase = Phase.INTRO
        self._room_vics = []
        self._searched_rooms = []
        self._current_rooms = [] # rooms that are currently being searched
        self._rooms_searched_by_me = [] # rooms that the agent searched
        self._presumably_empty_rooms = [] # rooms that the human declared empty
        self._found_victims = []
        self._collected_victims = []
        self._found_victim_logs = {}
        self._send_messages = []
        self._current_door = None
        self._team_members = []
        self._carrying_together = False
        self._remove = False
        self._goal_vic = None
        self._goal_loc = None
        self._human_loc = None
        self._distance_human = None
        self._distance_drop = None
        self._agent_loc = None
        self._todo = []
        self._last_processed_index = 0
        self._stored_first_message = None
        self._answered = False
        self._to_search = []
        self._carrying = False
        
        self._waiting = False # waiting for the human to responds
        
        self._waiting_with_patience = False # waiting for the human to show up
        self._patience = datetime.now() # until when to wait for the human to show up
        self._patience_log = 0 # how many seconds did we wait

        self._waiting_for_response = False # waiting for the human to respond
        self._response_patience = datetime.now() # until when to wait for the human to respond
        self._response_patience_log = 0 # how many seconds did we wait
        
        self._rescue = None
        self._recent_vic = None
        self._received_messages = []
        self._all_messages = []
        self._moving = False

    def initialize(self):
        # Initialization of the state tracker and navigation algorithm
        self._state_tracker = StateTracker(agent_id=self.agent_id)
        self._navigator = Navigator(agent_id=self.agent_id, action_set=self.action_set,
                                    algorithm=Navigator.A_STAR_ALGORITHM)

    def filter_observations(self, state):
        # Filtering of the world state before deciding on an action 
        return state

    def decide_on_actions(self, state):
        # Identify team members
        agent_name = state[self.agent_id]['obj_id']
        for member in state['World']['team_members']:
            if member != agent_name and member not in self._team_members:
                self._team_members.append(member)
        # Create a list of received messages from the human team member
        for mssg in self.received_messages:
            for member in self._team_members:
                if mssg.from_id == member and mssg.content not in self._received_messages:
                    self._received_messages.append(mssg.content)
        # Process messages from team members
        self._process_messages(state, self._team_members, self._condition)
        # Initialize and update trust beliefs for team members
        trustBeliefs = self._loadBelief(self._team_members, self._folder)
        trustBeliefs = self._trustBelief(self._team_members, trustBeliefs, self._folder, self._received_messages)

        # Check whether human is close in distance
        if state[{'is_human_agent': True}]:
            self._distance_human = 'close'
        if not state[{'is_human_agent': True}]:
            # Define distance between human and agent based on last known area locations
            if self._agent_loc in [1, 2, 3, 4, 5, 6, 7] and self._human_loc in [8, 9, 10, 11, 12, 13, 14]:
                self._distance_human = 'far'
            if self._agent_loc in [1, 2, 3, 4, 5, 6, 7] and self._human_loc in [1, 2, 3, 4, 5, 6, 7]:
                self._distance_human = 'close'
            if self._agent_loc in [8, 9, 10, 11, 12, 13, 14] and self._human_loc in [1, 2, 3, 4, 5, 6, 7]:
                self._distance_human = 'far'
            if self._agent_loc in [8, 9, 10, 11, 12, 13, 14] and self._human_loc in [8, 9, 10, 11, 12, 13, 14]:
                self._distance_human = 'close'

        # Define distance to drop zone based on last known area location
        if self._agent_loc in [1, 2, 5, 6, 8, 9, 11, 12]:
            self._distance_drop = 'far'
        if self._agent_loc in [3, 4, 7, 10, 13, 14]:
            self._distance_drop = 'close'

        # Check whether victims are currently being carried together by human and agent 
        for info in state.values():
            if 'is_human_agent' in info and self._human_name in info['name'] and len(
                    info['is_carrying']) > 0 and 'critical' in info['is_carrying'][0]['obj_id'] or \
                    'is_human_agent' in info and self._human_name in info['name'] and len(
                info['is_carrying']) > 0 and 'mild' in info['is_carrying'][0][
                'obj_id'] and self._rescue == 'together' and not self._moving:
                # If victim is being carried, add to collected victims memory
                if info['is_carrying'][0]['img_name'][8:-4] not in self._collected_victims:
                    self._collected_victims.append(info['is_carrying'][0]['img_name'][8:-4])
                self._carrying_together = True
            if 'is_human_agent' in info and self._human_name in info['name'] and len(info['is_carrying']) == 0:
                self._carrying_together = False
        # If carrying a victim together, let agent be idle (because joint actions are essentially carried out by the human)
        if self._carrying_together == True:
            return None, {}

        # Send the hidden score message for displaying and logging the score during the task, DO NOT REMOVE THIS
        self._send_message('Our score is ' + str(state['rescuebot']['score']) + '.', 'RescueBot')

        # Ongoing loop until the task is terminated, using different phases for defining the agent's behavior
        while True:

            DEF_OF_HIGH = 0

            # Determine the trust we have in the agent
            high_search_competence = trustBeliefs['search']['competence'] > DEF_OF_HIGH
            high_search_willingness = trustBeliefs['search']['willingness'] > DEF_OF_HIGH

            high_clear_competence = trustBeliefs['clear']['competence'] > DEF_OF_HIGH
            high_clear_willingness = trustBeliefs['clear']['willingness'] > DEF_OF_HIGH

            high_rescue_competence = trustBeliefs['rescue']['competence'] > DEF_OF_HIGH
            high_rescue_willingness = trustBeliefs['rescue']['willingness'] > DEF_OF_HIGH

            average_competence = np.average([
                trustBeliefs['search']['competence'],
                trustBeliefs['clear']['competence'],
                trustBeliefs['rescue']['competence']
            ])
            average_willingness = np.average([
                trustBeliefs['search']['willingness'],
                trustBeliefs['clear']['willingness'],
                trustBeliefs['rescue']['willingness']
            ])

            high_competence = average_competence > DEF_OF_HIGH
            high_willingness = average_willingness > DEF_OF_HIGH

            search_patience = round((trustBeliefs['search']['willingness'] + 2) * 15)
            clear_patience = round((trustBeliefs['clear']['willingness'] + 2) * 15)
            rescue_patience = round((trustBeliefs['rescue']['willingness'] + 2) * 15)

            shorter_search_patience = round(search_patience / 2)
            shorter_clear_patience = round(clear_patience / 2)
            shorter_rescue_patience = round(rescue_patience / 2)

            # STATUS: No trust belief system
            if Phase.INTRO == self._phase:
                # Send introduction message
                self._send_message('Hello! My name is RescueBot. Together we will collaborate and try to search and rescue the 8 victims on our right as quickly as possible. \
                Each critical victim (critically injured girl/critically injured elderly woman/critically injured man/critically injured dog) adds 6 points to our score, \
                each mild victim (mildly injured boy/mildly injured elderly man/mildly injured woman/mildly injured cat) 3 points. \
                If you are ready to begin our mission, you can simply start moving.', 'RescueBot')
                # Wait untill the human starts moving before going to the next phase, otherwise remain idle
                if not state[{'is_human_agent': True}]:
                    self._phase = Phase.FIND_NEXT_GOAL
                else:
                    return None, {}

            # STATUS: Trust belief system implemented by decision tree, QA
            if Phase.FIND_NEXT_GOAL == self._phase:
                # Definition of some relevant variables
                self._answered = False
                self._goal_vic = None
                self._goal_loc = None
                self._rescue = None
                self._moving = True
                remaining_zones = []
                remaining_vics = []
                remaining = {}
                # Identification of the location of the drop zones
                zones = self._get_drop_zones(state)
                # Identification of which victims still need to be rescued and on which location they should be dropped
                for info in zones:
                    if str(info['img_name'])[8:-4] not in self._collected_victims:
                        remaining_zones.append(info)
                        remaining_vics.append(str(info['img_name'])[8:-4])
                        remaining[str(info['img_name'])[8:-4]] = info['location']
                if remaining_zones:
                    self._remainingZones = remaining_zones
                    self._remaining = remaining
                # Remain idle if there are no victims left to rescue
                if not remaining_zones:
                    return None, {}
                
                def research_victim(vic, remaining):
                    # Define a previously found victim as target victim because all areas have been searched
                    self._goal_vic = vic
                    self._goal_loc = remaining[vic]
                    # Move to target victim
                    self._rescue = 'together'
                    self._send_message('Moving to ' + self._found_victim_logs[vic][
                        'room'] + ' to pick up ' + self._goal_vic + '. Please come there as well to help me carry ' + self._goal_vic + ' to the drop zone.',
                                        'RescueBot')
                    # Plan path to victim because the exact location is known (i.e., the agent found this victim)
                    if 'location' in self._found_victim_logs[vic].keys():
                        self._phase = Phase.PLAN_PATH_TO_VICTIM
                        return Idle.__name__, {'duration_in_ticks': 25}
                    # Plan path to area because the exact victim location is not known, only the area (i.e., human found this  victim)
                    if 'location' not in self._found_victim_logs[vic].keys():
                        self._phase = Phase.PLAN_PATH_TO_ROOM
                        return Idle.__name__, {'duration_in_ticks': 25}
                        
                def go_rescue_victim(vic, remaining, prefer_together):
                    # Define a previously found victim as target victim
                        self._goal_vic = vic
                        self._goal_loc = remaining[vic]
                        # Rescue together when victim is critical or when the human is weak and the victim is mildly injured
                        if 'critical' in vic or 'mild' in vic and prefer_together: #and self._condition == 'weak':
                            self._rescue = 'together'
                        # Rescue alone if the victim is mildly injured and the human not weak
                        if 'mild' in vic and not prefer_together: # self._condition != 'weak':
                            self._rescue = 'alone'
                        # Plan path to victim because the exact location is known (i.e., the agent found this victim)
                        if 'location' in self._found_victim_logs[vic].keys():
                            self._phase = Phase.PLAN_PATH_TO_VICTIM
                            return Idle.__name__, {'duration_in_ticks': 25}
                        # Plan path to area because the exact victim location is not known, only the area (i.e., human found this  victim)
                        if 'location' not in self._found_victim_logs[vic].keys():
                            self._phase = Phase.PLAN_PATH_TO_ROOM
                            return Idle.__name__, {'duration_in_ticks': 25}
                        
                def search_a_room():
                    # If there are no target victims found, visit an unsearched area to search for victims
                        self._phase = Phase.PICK_UNSEARCHED_ROOM

                # Check which victims can be rescued next because human or agent already found them
                for vic in remaining_vics:
                    if high_competence and high_willingness:
                        if vic in self._found_victims and vic not in self._todo:
                            go_rescue_victim(vic, remaining, False)
                        if vic not in self._found_victims or vic in self._found_victims and vic in self._todo and len(
                                self._searched_rooms) > 0:
                            search_a_room()
                        if vic in self._found_victims and vic in self._todo and len(self._searched_rooms) == 0:
                            return research_victim(vic, remaining)
                    if high_competence and not high_willingness:
                        if vic in self._found_victims and vic not in self._todo:
                            return go_rescue_victim(vic, remaining, False)
                        if vic in self._found_victims and vic in self._todo and len(self._searched_rooms) == 0:
                            return research_victim(vic, remaining)
                        if vic not in self._found_victims or vic in self._found_victims and vic in self._todo and len(
                                self._searched_rooms) > 0:
                            search_a_room()
                    if not high_competence:
                        if vic in self._found_victims and vic not in self._todo:
                            return go_rescue_victim(vic, remaining, True)
                        if vic not in self._found_victims or vic in self._found_victims and vic in self._todo and len(
                                self._searched_rooms) > 0:
                            search_a_room()
                        if vic in self._found_victims and vic in self._todo and len(self._searched_rooms) == 0:
                            research_victim(vic, remaining)

            # STATUS: Trust belief system implemented by decision tree, QA
            if Phase.PICK_UNSEARCHED_ROOM == self._phase:
                agent_location = state[self.agent_id]['location']
                # Identify which areas are not explored yet
                unsearched_rooms = [room['room_name'] for room in state.values()
                                   if 'class_inheritance' in room
                                   and 'Door' in room['class_inheritance']
                                   and room['room_name'] not in self._searched_rooms
                                   and room['room_name'] not in self._to_search]
                
                if not high_search_willingness:
                    for empty_room in self._presumably_empty_rooms:
                        if empty_room not in unsearched_rooms and empty_room not in self._current_rooms:
                            unsearched_rooms.append(empty_room)
                            self._searched_rooms.remove(empty_room)

                # If all areas have been searched but the task is not finished, start searching areas again
                if self._remainingZones and len(unsearched_rooms) == 0:
                    self._to_search = []
                    self._searched_rooms = []
                    self._send_messages = []
                    self.received_messages = []
                    self.received_messages_content = []
                    self._send_message('Going to re-search all areas.', 'RescueBot')
                    self._phase = Phase.FIND_NEXT_GOAL
                # If there are still areas to search, define which one to search next
                else:
                    # Identify the closest door when the agent did not search any areas yet
                    if self._current_door == None:
                        # Find all area entrance locations
                        self._door = state.get_room_doors(self._getClosestRoom(state, unsearched_rooms, agent_location))[
                            0]
                        self._doormat = \
                            state.get_room(self._getClosestRoom(state, unsearched_rooms, agent_location))[-1]['doormat']
                        # Workaround for one area because of some bug
                        if self._door['room_name'] == 'area 1':
                            self._doormat = (3, 5)
                        # Plan path to area
                        self._phase = Phase.PLAN_PATH_TO_ROOM
                    # Identify the closest door when the agent just searched another area
                    if self._current_door != None:
                        self._door = \
                            state.get_room_doors(self._getClosestRoom(state, unsearched_rooms, self._current_door))[0]
                        self._doormat = \
                            state.get_room(self._getClosestRoom(state, unsearched_rooms, self._current_door))[-1][
                                'doormat']
                        if self._door['room_name'] == 'area 1':
                            self._doormat = (3, 5)
                        self._phase = Phase.PLAN_PATH_TO_ROOM

                    self._send_message("I will search " + self._door['room_name'], 'RescueBot')

            # STATUS: Trust belief system not implemented (yet)
            if Phase.PLAN_PATH_TO_ROOM == self._phase:
                # Reset the navigator for a new path planning
                self._navigator.reset_full()

                # Check if there is a goal victim, and it has been found, but its location is not known
                if self._goal_vic \
                        and self._goal_vic in self._found_victims \
                        and 'location' not in self._found_victim_logs[self._goal_vic].keys():
                    # Retrieve the victim's room location and related information
                    victim_location = self._found_victim_logs[self._goal_vic]['room']
                    self._door = state.get_room_doors(victim_location)[0]
                    self._doormat = state.get_room(victim_location)[-1]['doormat']

                    # Handle special case for 'area 1'
                    if self._door['room_name'] == 'area 1':
                        self._doormat = (3, 5)

                    # Set the door location based on the doormat
                    doorLoc = self._doormat

                # If the goal victim's location is known, plan the route to the identified area
                else:
                    if self._door['room_name'] == 'area 1':
                        self._doormat = (3, 5)
                    doorLoc = self._doormat

                # Add the door location as a waypoint for navigation
                self._navigator.add_waypoints([doorLoc])
                # Follow the route to the next area to search
                self._phase = Phase.FOLLOW_PATH_TO_ROOM

            # STATUS: Trust belief system not implemented by design
            if Phase.FOLLOW_PATH_TO_ROOM == self._phase:
                # Check if the previously identified target victim was rescued by the human
                if self._goal_vic and self._goal_vic in self._collected_victims:
                    # Reset current door and switch to finding the next goal
                    self._current_door = None
                    self._phase = Phase.FIND_NEXT_GOAL

                # Check if the human found the previously identified target victim in a different room
                if self._goal_vic \
                        and self._goal_vic in self._found_victims \
                        and self._door['room_name'] != self._found_victim_logs[self._goal_vic]['room']:
                    self._current_door = None
                    self._phase = Phase.FIND_NEXT_GOAL

                # Check if the human already searched the previously identified area without finding the target victim
                if self._door['room_name'] in self._searched_rooms and self._goal_vic not in self._found_victims:
                    self._current_door = None
                    self._phase = Phase.FIND_NEXT_GOAL

                # Move to the next area to search
                else:
                    # Update the state tracker with the current state
                    self._state_tracker.update(state)

                    # Explain why the agent is moving to the specific area, either:
                    # [-] it contains the current target victim
                    # [-] it is the closest un-searched area
                    if self._goal_vic in self._found_victims \
                            and str(self._door['room_name']) == self._found_victim_logs[self._goal_vic]['room'] \
                            and not self._remove:
                        if self._condition == 'weak':
                            self._send_message('Moving to ' + str(
                                self._door['room_name']) + ' to pick up ' + self._goal_vic + ' together with you.',
                                              'RescueBot')
                        else:
                            self._send_message(
                                'Moving to ' + str(self._door['room_name']) + ' to pick up ' + self._goal_vic + '.',
                                'RescueBot')

                    if self._goal_vic not in self._found_victims and not self._remove or not self._goal_vic and not self._remove:
                        self._send_message(
                            'Moving to ' + str(self._door['room_name']) + ' because it is the closest unsearched area.',
                            'RescueBot')

                    # Set the current door based on the current location
                    self._current_door = self._door['location']

                    # Retrieve move actions to execute
                    action = self._navigator.get_move_action(self._state_tracker)
                    # Check for obstacles blocking the path to the area and handle them if needed
                    if action is not None:
                        # Remove obstacles blocking the path to the area 
                        for info in state.values():
                            if 'class_inheritance' in info and 'ObstacleObject' in info[
                                'class_inheritance'] and 'stone' in info['obj_id'] and info['location'] not in [(9, 4),
                                                                                                                (9, 7),
                                                                                                                (9, 19),
                                                                                                                (21,
                                                                                                                 19)]:
                                self._send_message('Reaching ' + str(self._door['room_name'])
                                                   + ' will take a bit longer because I found stones blocking my path.',
                                                   'RescueBot')
                                return RemoveObject.__name__, {'object_id': info['obj_id']}
                        return action, {}
                    # Identify and remove obstacles if they are blocking the entrance of the area
                    self._phase = Phase.REMOVE_OBSTACLE_IF_NEEDED
            
            # STATUS: Trust belief system with timers implemented, but timer values should become trust value based
            if Phase.REMOVE_OBSTACLE_IF_NEEDED == self._phase:
                objects = []
                agent_location = state[self.agent_id]['location']
                # Identify which obstacle is blocking the entrance
                for info in state.values():
                    if 'class_inheritance' in info and 'ObstacleObject' in info['class_inheritance'] and 'rock' in info[
                        'obj_id']:
                        objects.append(info)
                        # Communicate which obstacle is blocking the entrance
                        if self._answered == False and not self._remove and not self._waiting:
                            self._send_message('Found rock blocking ' + str(self._door['room_name']) + '. Please decide whether to "Remove" or "Continue" searching. \n \n \
                                Important features to consider are: \n safe - victims rescued: ' + str(
                                self._collected_victims) + ' \n explore - areas searched: area ' + str(
                                self._searched_rooms).replace('area ', '') + ' \
                                \n clock - removal time: 5 seconds \n afstand - distance between us: ' + self._distance_human + 
                                '\n Please respond withitn ' + str(clear_patience) + ' seconds.',
                                              'RescueBot')
                            self._waiting = True
                            self._waiting_for_response = True
                            self._response_patience = datetime.now() + timedelta(seconds=clear_patience)
                            self._response_patience_log = clear_patience
                        # Determine the next area to explore if the human tells the agent not to remove the obstacle
                        if self.received_messages_content and self.received_messages_content[
                            -1] == 'Continue' and not self._remove:
                            self._answered = True
                            self._waiting = False
                            self._waiting_for_response = False
                            # Add area to the to do list
                            self._to_search.append(self._door['room_name'])
                            self._phase = Phase.FIND_NEXT_GOAL
                        # Wait for the human to help removing the obstacle and remove the obstacle together
                        if self.received_messages_content and self.received_messages_content[
                            -1] == 'Remove' or self._remove:
                            if not self._remove:
                                self._answered = True
                                self._waiting_for_response = False
                            # Tell the human to come over and be idle untill human arrives, leave after a timer
                            if not state[{'is_human_agent': True}]:
                                self._send_message('Please come to ' + str(self._door['room_name']) + ' to remove rock.',
                                                  'RescueBot')
                                self._waiting = True
                                self._waiting_with_patience = True
                                self._patience = datetime.now() + timedelta(seconds=clear_patience)
                                self._patience_log = clear_patience
                                self._send_message(f"Started waiting {str(clear_patience)} seconds.", "RescueBot")
                            # Tell the human to remove the obstacle when he/she arrives
                            if state[{'is_human_agent': True}]:
                                self._send_message('Lets remove rock blocking ' + str(self._door['room_name']) + '!',
                                                  'RescueBot')
                                return None, {}
                        # If the human does not show up in time, go do something else
                        if self._waiting_with_patience and datetime.now() > self._patience:
                            self._send_message(f"I waited for {self._patience_log} seconds, but you did not show up to destroy obstacles.", "RescueBot")
                            self._answered = True
                            self._waiting = False
                            self._waiting_with_patience = False
                            self._remove = False
                            # Add area to the to do list
                            self._to_search.append(self._door['room_name'])
                            self._phase = Phase.FIND_NEXT_GOAL
                            return None, {}
                        # If the human does not respond in time, go do something else
                        if self._waiting_for_response and datetime.now() > self._response_patience:
                            self._send_message(f"I waited for {self._response_patience_log} seconds for you to respond if to destroy obstacles. I will go do something else.", "RescueBot")
                            self._answered = True
                            self._waiting = False
                            self._waiting_for_response = False
                            self._remove = False
                            # Add area to the to do list
                            self._to_search.append(self._door['room_name'])
                            self._phase = Phase.FIND_NEXT_GOAL
                            return None, {}
                        # Remain idle untill the human communicates what to do with the identified obstacle 
                        else:
                            return None, {}

                    if 'class_inheritance' in info and 'ObstacleObject' in info['class_inheritance'] and 'tree' in info[
                        'obj_id']:
                        objects.append(info)
                        # Communicate which obstacle is blocking the entrance
                        if self._answered == False and not self._remove and not self._waiting:
                            self._send_message('Found tree blocking  ' + str(self._door['room_name']) + '. Please decide whether to "Remove" or "Continue" searching. \n \n \
                                Important features to consider are: \n safe - victims rescued: ' + str(
                                self._collected_victims) + '\n explore - areas searched: area ' + str(
                                self._searched_rooms).replace('area ', '') + ' \
                                \n clock - removal time: 10 seconds \n Please respond withitn ' + str(shorter_clear_patience) + ' seconds.', 'RescueBot')
                            self._waiting = True
                            self._waiting_for_response = True
                            self._response_patience = datetime.now() + timedelta(seconds=shorter_clear_patience)
                            self._response_patience_log = shorter_clear_patience
                        # Determine the next area to explore if the human tells the agent not to remove the obstacle
                        if self.received_messages_content and self.received_messages_content[
                            -1] == 'Continue' and not self._remove:
                            self._answered = True
                            self._waiting = False
                            self._waiting_for_response = False
                            # Add area to the to do list
                            self._to_search.append(self._door['room_name'])
                            self._phase = Phase.FIND_NEXT_GOAL
                        # Remove the obstacle if the human tells the agent to do so
                        if self.received_messages_content and self.received_messages_content[
                            -1] == 'Remove' or self._remove:
                            if not self._remove:
                                self._answered = True
                                self._waiting = False
                                self._waiting_for_response = False
                                self._send_message('Removing tree blocking ' + str(self._door['room_name']) + '.',
                                                  'RescueBot')
                            if self._remove:
                                self._send_message('Removing tree blocking ' + str(
                                    self._door['room_name']) + ' because you asked me to.', 'RescueBot')
                            self._phase = Phase.ENTER_ROOM
                            self._remove = False
                            return RemoveObject.__name__, {'object_id': info['obj_id']}
                        # If the human does not respond in time, remove it
                        if self._waiting_for_response and datetime.now() > self._response_patience:
                            self._send_message(f"I waited for {self._response_patience_log} seconds for you to respond if to destory obstacles. I will remove the tree.", "RescueBot")
                            self._answered = True
                            self._waiting = False
                            self._waiting_for_response = False
                            self._remove = False
                            self._phase = Phase.ENTER_ROOM
                            return RemoveObject.__name__, {'object_id': info['obj_id']}
                        # Remain idle untill the human communicates what to do with the identified obstacle
                        else:
                            return None, {}

                    if 'class_inheritance' in info and 'ObstacleObject' in info['class_inheritance'] and 'stone' in \
                            info['obj_id']:
                        objects.append(info)
                        # Communicate which obstacle is blocking the entrance
                        if self._answered == False and not self._remove and not self._waiting:
                            self._send_message('Found stones blocking  ' + str(self._door['room_name']) + '. Please decide whether to "Remove together", "Remove alone", or "Continue" searching. \n \n \
                                Important features to consider are: \n safe - victims rescued: ' + str(
                                self._collected_victims) + ' \n explore - areas searched: area ' + str(
                                self._searched_rooms).replace('area', '') + ' \
                                \n clock - removal time together: 3 seconds \n afstand - distance between us: ' + self._distance_human + '\n clock - removal time alone: 20 seconds' +
                                '\n Please respond withitn ' + str(shorter_clear_patience) + ' seconds.',
                                              'RescueBot')
                            self._waiting = True
                            self._waiting_for_response = True
                            self._response_patience = datetime.now() + timedelta(seconds=shorter_clear_patience)
                            self._response_patience_log = shorter_clear_patience
                        # Determine the next area to explore if the human tells the agent not to remove the obstacle          
                        if self.received_messages_content and self.received_messages_content[
                            -1] == 'Continue' and not self._remove:
                            self._answered = True
                            self._waiting = False
                            # Add area to the to do list
                            self._to_search.append(self._door['room_name'])
                            self._phase = Phase.FIND_NEXT_GOAL
                        # Remove the obstacle alone if the human decides so
                        if self.received_messages_content and self.received_messages_content[
                            -1] == 'Remove alone' and not self._remove:
                            self._answered = True
                            self._waiting = False
                            self._waiting_for_response = False
                            self._send_message('Removing stones blocking ' + str(self._door['room_name']) + '.',
                                              'RescueBot')
                            self._phase = Phase.ENTER_ROOM
                            self._remove = False
                            return RemoveObject.__name__, {'object_id': info['obj_id']}
                        # Remove the obstacle together if the human decides so
                        if self.received_messages_content and self.received_messages_content[
                            -1] == 'Remove together' or self._remove:
                            if not self._remove:
                                self._answered = True
                                self._waiting_for_response = False
                            # Tell the human to come over and be idle untill human arrives
                            if not state[{'is_human_agent': True}]:
                                self._send_message(
                                    'Please come to ' + str(self._door['room_name']) + ' to remove stones together.',
                                    'RescueBot')
                                self._waiting = True
                                self._waiting_with_patience = True
                                self._patience = datetime.now() + timedelta(seconds=shorter_clear_patience)
                                self._patience_log = shorter_clear_patience
                                self._send_message(f"Started waiting {str(shorter_clear_patience)} seconds.", "RescueBot")
                            # Tell the human to remove the obstacle when he/she arrives
                            if state[{'is_human_agent': True}]:
                                self._send_message('Lets remove stones blocking ' + str(self._door['room_name']) + '!',
                                                  'RescueBot')
                                return None, {}
                        # If the human responds to slowly, go do something else
                        if self._waiting_with_patience and datetime.now() > self._patience:
                            self._send_message(f"I waited for {self._patience_log} seconds, but you did not show up to rescue victims.", "RescueBot")
                            self._answered = True
                            self._waiting = False
                            self._waiting_with_patience = False
                            # Add area to the to do list
                            self._to_search.append(self._door['room_name'])
                            self._phase = Phase.FIND_NEXT_GOAL
                            return None, {}
                        # If the human does not respond in time, remove it
                        if self._waiting_for_response and datetime.now() > self._response_patience:
                            self._send_message(f"I waited for {self._response_patience_log} seconds for you to respond if to destory obstacles. I will remove the stone alone.", "RescueBot")
                            self._answered = True
                            self._waiting = False
                            self._waiting_for_response = False
                            self._remove = False
                            self._phase = Phase.ENTER_ROOM
                            return RemoveObject.__name__, {'object_id': info['obj_id']}
                        # Remain idle until the human communicates what to do with the identified obstacle
                        else:
                            return None, {}
                # If no obstacles are blocking the entrance, enter the area
                if len(objects) == 0:
                    self._answered = False
                    self._remove = False
                    self._waiting = False
                    self._phase = Phase.ENTER_ROOM

            # STATUS: Trust belief system implemented by decision tree, QA
            if Phase.ENTER_ROOM == self._phase:
                self._answered = False

                # Check if the target victim has been rescued by the human, and switch to finding the next goal
                if self._goal_vic in self._collected_victims:
                    self._current_door = None
                    self._phase = Phase.FIND_NEXT_GOAL

                # Check if the target victim is found in a different area, and start moving there
                if self._goal_vic in self._found_victims \
                        and self._door['room_name'] != self._found_victim_logs[self._goal_vic]['room']:
                    self._current_door = None
                    self._phase = Phase.FIND_NEXT_GOAL

                # Check if area already searched without finding the target victim, and plan to search another area
                if high_search_willingness and self._door['room_name'] in self._searched_rooms and self._goal_vic not in self._found_victims:
                    self._current_door = None
                    self._phase = Phase.FIND_NEXT_GOAL

                # Enter the area and plan to search it
                else:
                    self._state_tracker.update(state)

                    action = self._navigator.get_move_action(self._state_tracker)
                    # If there is a valid action, return it; otherwise, plan to search the room
                    if action is not None:
                        return action, {}
                    self._phase = Phase.PLAN_ROOM_SEARCH_PATH

            # STATUS: Trust belief system not implemented by design
            if Phase.PLAN_ROOM_SEARCH_PATH == self._phase:
                # Extract the numeric location from the room name and set it as the agent's location
                self._agent_loc = int(self._door['room_name'].split()[-1])

                # Store the locations of all area tiles in the current room
                room_tiles = [info['location'] for info in state.values()
                             if 'class_inheritance' in info
                             and 'AreaTile' in info['class_inheritance']
                             and 'room_name' in info
                             and info['room_name'] == self._door['room_name']]
                self._roomtiles = room_tiles

                # Make the plan for searching the area
                self._navigator.reset_full()
                self._navigator.add_waypoints(self._efficientSearch(room_tiles))

                # Initialize variables for storing room victims and switch to following the room search path
                self._room_vics = []
                self._phase = Phase.FOLLOW_ROOM_SEARCH_PATH

            # STATUS: Trust belief system implemented by decision tree, QA
            if Phase.FOLLOW_ROOM_SEARCH_PATH == self._phase:
                # Search the area
                self._state_tracker.update(state)
                action = self._navigator.get_move_action(self._state_tracker)
                if action != None:
                    # Identify victims present in the area
                    for info in state.values():
                        if 'class_inheritance' in info and 'CollectableBlock' in info['class_inheritance']:
                            vic = str(info['img_name'][8:-4])
                            # Remember which victim the agent found in this area
                            if vic not in self._room_vics:
                                self._room_vics.append(vic)

                            # Identify the exact location of the victim that was found by the human earlier
                            if vic in self._found_victims and 'location' not in self._found_victim_logs[vic].keys():
                                self._recent_vic = vic
                                # Add the exact victim location to the corresponding dictionary
                                self._found_victim_logs[vic] = {'location': info['location'],
                                                                'room': self._door['room_name'],
                                                                'obj_id': info['obj_id']}
                                if vic == self._goal_vic:
                                    # Communicate which victim was found
                                    self._send_message('Found ' + vic + ' in ' + self._door[
                                        'room_name'] + ' because you told me ' + vic + ' was located here.',
                                                      'RescueBot')
                                    # Add the area to the list with searched areas
                                    if self._door['room_name'] not in self._searched_rooms:
                                        self._searched_rooms.append(self._door['room_name'])
                                    if self._door['room_name'] in self._presumably_empty_rooms:
                                        self._presumably_empty_rooms.remove(self._door['room_name'])
                                    if self._door['room_name'] not in self._rooms_searched_by_me:
                                        self._rooms_searched_by_me.append(self._door['room_name'])
                                    # Do not continue searching the rest of the area but start planning to rescue the victim
                                    self._phase = Phase.FIND_NEXT_GOAL

                            # Identify injured victim in the area
                            if 'healthy' not in vic and vic not in self._found_victims:
                                self._recent_vic = vic
                                # Add the victim and the location to the corresponding dictionary
                                self._found_victims.append(vic)
                                self._found_victim_logs[vic] = {'location': info['location'],
                                                                'room': self._door['room_name'],
                                                                'obj_id': info['obj_id']}
                                # Communicate which victim the agent found and ask the human whether to rescue the victim now or at a later stage
                                if 'mild' in vic and self._answered == False and not self._waiting:
                                    self._send_message('Found ' + vic + ' in ' + self._door['room_name'] + '. Please decide whether to "Rescue together", "Rescue alone", or "Continue" searching. \n \n \
                                        Important features to consider are: \n safe - victims rescued: ' + str(
                                        self._collected_victims) + '\n explore - areas searched: area ' + str(
                                        self._searched_rooms).replace('area ', '') + '\n \
                                        clock - extra time when rescuing alone: 15 seconds \n afstand - distance between us: ' + self._distance_human +
                                        '\n Please respond withitn ' + str(shorter_rescue_patience) + ' seconds.',
                                                      'RescueBot')
                                    self._waiting = True
                                    self._waiting_for_response = True
                                    self._response_patience = datetime.now() + timedelta(seconds=shorter_rescue_patience)
                                    self._response_patience_log = shorter_rescue_patience

                                if 'critical' in vic and self._answered == False and not self._waiting:
                                    self._send_message('Found ' + vic + ' in ' + self._door['room_name'] + '. Please decide whether to "Rescue" or "Continue" searching. \n\n \
                                        Important features to consider are: \n explore - areas searched: area ' + str(
                                        self._searched_rooms).replace('area',
                                                                      '') + ' \n safe - victims rescued: ' + str(
                                        self._collected_victims) + '\n \
                                        afstand - distance between us: ' + self._distance_human +
                                        '\n Please respond withitn ' + str(rescue_patience) + ' seconds.', 'RescueBot')
                                    self._waiting = True
                                    self._waiting_for_response = True
                                    self._response_patience = datetime.now() + timedelta(seconds=rescue_patience)
                                    self._response_patience_log = rescue_patience
                                    # Execute move actions to explore the area
                    return action, {}

                # Communicate that the agent did not find the target victim in the area while the human previously communicated the victim was located here
                if self._goal_vic in self._found_victims and self._goal_vic not in self._room_vics and \
                        self._found_victim_logs[self._goal_vic]['room'] == self._door['room_name']:
                    self._send_message(self._goal_vic + ' not present in ' + str(self._door[
                                                                                    'room_name']) + ' because I searched the whole area without finding ' + self._goal_vic + '.',
                                      'RescueBot')
                    # Remove the victim location from memory
                    self._found_victim_logs.pop(self._goal_vic, None)
                    self._found_victims.remove(self._goal_vic)
                    self._room_vics = []
                    # Reset received messages (bug fix)
                    self.received_messages = []
                    self.received_messages_content = []
                # Add the area to the list of searched areas
                if self._door['room_name'] not in self._searched_rooms:
                    self._searched_rooms.append(self._door['room_name'])
                if self._door['room_name'] in self._presumably_empty_rooms:
                    self._presumably_empty_rooms.remove(self._door['room_name'])
                if self._door['room_name'] not in self._rooms_searched_by_me:
                    self._rooms_searched_by_me.append(self._door['room_name'])
                # Make a plan to rescue a found critically injured victim if the human decides so
                if self.received_messages_content and self.received_messages_content[
                    -1] == 'Rescue' and 'critical' in self._recent_vic:
                    self._rescue = 'together'
                    self._answered = True
                    self._waiting = False
                    # Tell the human to come over and help carry the critically injured victim
                    if not state[{'is_human_agent': True}]:
                        self._send_message('Please come to ' + str(self._door['room_name']) + ' to carry ' + str(
                            self._recent_vic) + ' together.', 'RescueBot')
                        self._waiting = True
                        self._waiting_with_patience = True
                        self._send_message(f"Started waiting {rescue_patience} seconds", "RescueBot")
                        self._patience = datetime.now() + timedelta(seconds=rescue_patience)
                        self._patience_log = rescue_patience
                    # Tell the human to carry the critically injured victim together
                    if state[{'is_human_agent': True}]:
                        self._send_message('Lets carry ' + str(
                            self._recent_vic) + ' together! Please wait until I moved on top of ' + str(
                            self._recent_vic) + '.', 'RescueBot')
                    self._goal_vic = self._recent_vic
                    self._recent_vic = None
                    self._phase = Phase.PLAN_PATH_TO_VICTIM
                # Make a plan to rescue a found mildly injured victim together if the human decides so
                if self.received_messages_content and self.received_messages_content[
                    -1] == 'Rescue together' and 'mild' in self._recent_vic:
                    self._rescue = 'together'
                    self._answered = True
                    self._waiting = False
                    # Tell the human to come over and help carry the mildly injured victim
                    if not state[{'is_human_agent': True}]:
                        self._send_message('Please come to ' + str(self._door['room_name']) + ' to carry ' + str(
                            self._recent_vic) + ' together.', 'RescueBot')
                        self._waiting = True
                        self._waiting_with_patience = True
                        self._send_message(f"Started waiting {shorter_rescue_patience} seconds", "RescueBot")
                        self._patience = datetime.now() + timedelta(seconds=shorter_rescue_patience)
                        self._patience_log = shorter_rescue_patience
                    # Tell the human to carry the mildly injured victim together
                    if state[{'is_human_agent': True}]:
                        self._send_message('Lets carry ' + str(
                            self._recent_vic) + ' together! Please wait until I moved on top of ' + str(
                            self._recent_vic) + '.', 'RescueBot')
                    self._goal_vic = self._recent_vic
                    self._recent_vic = None
                    self._phase = Phase.PLAN_PATH_TO_VICTIM
                # Make a plan to rescue the mildly injured victim alone if the human decides so, and communicate this to the human
                if self.received_messages_content and self.received_messages_content[
                    -1] == 'Rescue alone' and 'mild' in self._recent_vic:
                    self._send_message('Picking up ' + self._recent_vic + ' in ' + self._door['room_name'] + '.',
                                      'RescueBot')
                    self._rescue = 'alone'
                    self._answered = True
                    self._waiting = False
                    self._waiting_for_response = False
                    self._goal_vic = self._recent_vic
                    self._goal_loc = self._remaining[self._goal_vic]
                    self._recent_vic = None
                    self._phase = Phase.PLAN_PATH_TO_VICTIM
                # Continue searching other areas if the human decides so
                if self.received_messages_content and self.received_messages_content[-1] == 'Continue':
                    self._answered = True
                    self._waiting = False
                    self._waiting_for_response = False
                    self._todo.append(self._recent_vic)
                    self._recent_vic = None
                    self._phase = Phase.FIND_NEXT_GOAL
                if self._waiting_for_response and datetime.now() > self._response_patience and 'mild' in self._recent_vic:
                    self._send_message(
                        f"I waited for {self._response_patience_log} seconds for you to respond if to rescue the victim. I will remove rescue the victim alone.",
                        "RescueBot")
                    self._rescue = 'alone'
                    self._answered = True
                    self._waiting = False
                    self._waiting_for_response = False
                    self._goal_vic = self._recent_vic
                    self._goal_loc = self._remaining[self._goal_vic]
                    self._recent_vic = None
                    self._phase = Phase.PLAN_PATH_TO_VICTIM
                if self._waiting_for_response and datetime.now() > self._response_patience and 'critical' in self._recent_vic:
                    self._send_message(
                        f"I waited for {self._response_patience_log} seconds for you to respond if to rescue the victim. I will go do something else.",
                        "RescueBot")
                    self._answered = True
                    self._waiting = False
                    self._waiting_for_response = False
                    self._todo.append(self._recent_vic)
                    self._recent_vic = None
                    self._phase = Phase.FIND_NEXT_GOAL
                # Remain idle untill the human communicates to the agent what to do with the found victim
                if self.received_messages_content and self._waiting and self.received_messages_content[
                    -1] != 'Rescue' and self.received_messages_content[-1] != 'Continue':
                    return None, {}
                # Find the next area to search when the agent is not waiting for an answer from the human or occupied with rescuing a victim
                if not self._waiting and not self._rescue:
                    self._recent_vic = None
                    self._phase = Phase.FIND_NEXT_GOAL
                return Idle.__name__, {'duration_in_ticks': 25}

            # STATUS: Trust belief system not implemented by design
            if Phase.PLAN_PATH_TO_VICTIM == self._phase:
                # Plan the path to a found victim using its location
                self._navigator.reset_full()
                self._navigator.add_waypoints([self._found_victim_logs[self._goal_vic]['location']])
                # Follow the path to the found victim
                self._phase = Phase.FOLLOW_PATH_TO_VICTIM

            # STATUS: Trust belief system not implemented by design
            if Phase.FOLLOW_PATH_TO_VICTIM == self._phase:
                # Start searching for other victims if the human already rescued the target victim
                if self._goal_vic and self._goal_vic in self._collected_victims:
                    self._phase = Phase.FIND_NEXT_GOAL

                # Move towards the location of the found victim
                else:
                    self._state_tracker.update(state)

                    action = self._navigator.get_move_action(self._state_tracker)
                    # If there is a valid action, return it; otherwise, switch to taking the victim
                    if action is not None:
                        return action, {}
                    self._phase = Phase.TAKE_VICTIM

            # STATUS: Trust belief system implemented by decision tree, QA
            if Phase.TAKE_VICTIM == self._phase:
                # Store all area tiles in a list
                room_tiles = [info['location'] for info in state.values()
                             if 'class_inheritance' in info
                             and 'AreaTile' in info['class_inheritance']
                             and 'room_name' in info
                             and info['room_name'] == self._found_victim_logs[self._goal_vic]['room']]
                self._roomtiles = room_tiles
                objects = []
                # When the victim has to be carried by human and agent together, check whether human has arrived at the victim's location
                for info in state.values():
                    # When the victim has to be carried by human and agent together, check whether human has arrived at the victim's location
                    if 'class_inheritance' in info and 'CollectableBlock' in info['class_inheritance'] and 'critical' in \
                            info['obj_id'] and info['location'] in self._roomtiles or \
                            'class_inheritance' in info and 'CollectableBlock' in info[
                        'class_inheritance'] and 'mild' in info['obj_id'] and info[
                        'location'] in self._roomtiles and self._rescue == 'together' or \
                            self._goal_vic in self._found_victims and self._goal_vic in self._todo and len(
                        self._searched_rooms) == 0 and 'class_inheritance' in info and 'CollectableBlock' in info[
                        'class_inheritance'] and 'critical' in info['obj_id'] and info['location'] in self._roomtiles or \
                            self._goal_vic in self._found_victims and self._goal_vic in self._todo and len(
                        self._searched_rooms) == 0 and 'class_inheritance' in info and 'CollectableBlock' in info[
                        'class_inheritance'] and 'mild' in info['obj_id'] and info['location'] in self._roomtiles:
                        objects.append(info)
                        if self._waiting_with_patience and datetime.now() >  self._patience:
                            self._send_message(f"I waited for {self._patience_log} seconds, but you didn't show up to rescue the victim.", "RescueBot")
                            self._answered = True
                            self._waiting = False
                            self._waiting_with_patience = False
                            self._todo.append(self._recent_vic)
                            self._recent_vic = None
                            self._phase = Phase.FIND_NEXT_GOAL
                            return None, {}
                        # Remain idle when the human has not arrived at the location
                        if not self._human_name in info['name']:
                            self._waiting = True
                            self._moving = False
                            return None, {}
                # Add the victim to the list of rescued victims when it has been picked up
                if len(objects) == 0 and 'critical' in self._goal_vic or len(
                        objects) == 0 and 'mild' in self._goal_vic and self._rescue == 'together':
                    self._waiting = False
                    if self._goal_vic not in self._collected_victims:
                        self._collected_victims.append(self._goal_vic)
                    self._carrying_together = True
                    # Determine the next victim to rescue or search
                    self._phase = Phase.FIND_NEXT_GOAL
                # When rescuing mildly injured victims alone, pick the victim up and plan the path to the drop zone
                if 'mild' in self._goal_vic and self._rescue == 'alone':
                    self._phase = Phase.PLAN_PATH_TO_DROPPOINT
                    if self._goal_vic not in self._collected_victims:
                        self._collected_victims.append(self._goal_vic)
                    self._carrying = True
                    return CarryObject.__name__, {'object_id': self._found_victim_logs[self._goal_vic]['obj_id'],
                                                  'human_name': self._human_name}

            # STATUS: Trust belief system not implemented by design
            if Phase.PLAN_PATH_TO_DROPPOINT == self._phase:
                self._navigator.reset_full()
                # Plan the path to the drop zone
                self._navigator.add_waypoints([self._goal_loc])
                # Follow the path to the drop zone
                self._phase = Phase.FOLLOW_PATH_TO_DROPPOINT

            # STATUS: Trust belief system not implemented by design
            if Phase.FOLLOW_PATH_TO_DROPPOINT == self._phase:
                # Communicate that the agent is transporting a mildly injured victim alone to the drop zone
                if 'mild' in self._goal_vic and self._rescue == 'alone':
                    self._send_message('Transporting ' + self._goal_vic + ' to the drop zone.', 'RescueBot')
                self._state_tracker.update(state)
                # Follow the path to the drop zone
                action = self._navigator.get_move_action(self._state_tracker)
                if action is not None:
                    return action, {}
                # Drop the victim at the drop zone
                self._phase = Phase.DROP_VICTIM

            # STATUS: Trust belief system not implemented by design
            if Phase.DROP_VICTIM == self._phase:
                # Communicate that the agent delivered a mildly injured victim alone to the drop zone
                if 'mild' in self._goal_vic and self._rescue == 'alone':
                    self._send_message('Delivered ' + self._goal_vic + ' at the drop zone.', 'RescueBot')
                # Identify the next target victim to rescue
                self._phase = Phase.FIND_NEXT_GOAL
                self._rescue = None
                self._current_door = None
                self._tick = state['World']['nr_ticks']
                self._carrying = False
                # Drop the victim on the correct location on the drop zone
                return Drop.__name__, {'human_name': self._human_name}

    def _get_drop_zones(self, state):
        '''
        @return list of drop zones (their full dict), in order (the first one is the
        place that requires the first drop)
        '''
        places = state[{'is_goal_block': True}]
        places.sort(key=lambda info: info['location'][1])
        zones = []
        for place in places:
            if place['drop_zone_nr'] == 0:
                zones.append(place)
        return zones

    def _process_messages(self, state, teamMembers, condition):
        '''
        process incoming messages received from the team members
        '''

        receivedMessages = {}
        # Create a dictionary with a list of received messages from each team member
        for member in teamMembers:
            receivedMessages[member] = []
        for mssg in self.received_messages:
            for member in teamMembers:
                if mssg.from_id == member:
                    receivedMessages[member].append(mssg.content)
        # Check the content of the received messages
        for mssgs in receivedMessages.values():
            for msg in mssgs:
                # If a received message involves team members searching areas, add these areas to the memory of areas that have been explored
                if msg.startswith("Search:"):
                    area = 'area ' + msg.split()[-1]
                    if area not in self._searched_rooms:
                        self._searched_rooms.append(area)
                    if area not in self._current_rooms:
                        for room in self._current_rooms:
                            if room not in self._presumably_empty_rooms:
                                self._presumably_empty_rooms.append(room)
                            self._current_rooms.remove(room)
                        self._current_rooms.append(area)
                    for area in self._presumably_empty_rooms:
                        if area in self._rooms_searched_by_me:
                            self._presumably_empty_rooms.remove(area)
                # If a received message involves team members finding victims, add these victims and their locations to memory
                if msg.startswith("Found:"):
                    # Identify which victim and area it concerns
                    if len(msg.split()) == 6:
                        foundVic = ' '.join(msg.split()[1:4])
                    else:
                        foundVic = ' '.join(msg.split()[1:5])
                    loc = 'area ' + msg.split()[-1]
                    # Add the area to the memory of searched areas
                    if loc not in self._searched_rooms:
                        self._searched_rooms.append(loc)
                    # Remove the area from the empty rooms
                    if loc in self._presumably_empty_rooms:
                        self._presumably_empty_rooms.remove(loc)
                    # Add the victim and its location to memory
                    if foundVic not in self._found_victims:
                        self._found_victims.append(foundVic)
                        self._found_victim_logs[foundVic] = {'room': loc}
                    if foundVic in self._found_victims and self._found_victim_logs[foundVic]['room'] != loc:
                        self._found_victim_logs[foundVic] = {'room': loc}
                    # Decide to help the human carry a found victim when the human's condition is 'weak'
                    if condition == 'weak':
                        self._rescue = 'together'
                    # Add the found victim to the to do list when the human's condition is not 'weak'
                    if 'mild' in foundVic and condition != 'weak':
                        self._todo.append(foundVic)
                # If a received message involves team members rescuing victims, add these victims and their locations to memory
                if msg.startswith('Collect:'):
                    # Identify which victim and area it concerns
                    if len(msg.split()) == 6:
                        collectVic = ' '.join(msg.split()[1:4])
                    else:
                        collectVic = ' '.join(msg.split()[1:5])
                    loc = 'area ' + msg.split()[-1]
                    # Add the area to the memory of searched areas
                    if loc not in self._searched_rooms:
                        self._searched_rooms.append(loc)
                    # Remove the area from the empty rooms
                    if loc in self._presumably_empty_rooms:
                        self._presumably_empty_rooms.remove(loc)
                    # Add the victim and location to the memory of found victims
                    if collectVic not in self._found_victims:
                        self._found_victims.append(collectVic)
                        self._found_victim_logs[collectVic] = {'room': loc}
                    if collectVic in self._found_victims and self._found_victim_logs[collectVic]['room'] != loc:
                        self._found_victim_logs[collectVic] = {'room': loc}
                    # Add the victim to the memory of rescued victims when the human's condition is not weak
                    if condition != 'weak' and collectVic not in self._collected_victims:
                        self._collected_victims.append(collectVic)
                    # Decide to help the human carry the victim together when the human's condition is weak
                    if condition == 'weak':
                        self._rescue = 'together'
                # If a received message involves team members asking for help with removing obstacles, add their location to memory and come over
                if msg.startswith('Remove:'):
                    # Come over immediately when the agent is not carrying a victim
                    if not self._carrying:
                        # Identify at which location the human needs help
                        area = 'area ' + msg.split()[-1]
                        self._door = state.get_room_doors(area)[0]
                        self._doormat = state.get_room(area)[-1]['doormat']
                        if area in self._searched_rooms:
                            self._searched_rooms.remove(area)
                        # Clear received messages (bug fix)
                        self._all_messages.append(msg)
                        self.received_messages = []
                        self.received_messages_content = []
                        self._moving = True
                        self._remove = True
                        if self._waiting and self._recent_vic:
                            self._todo.append(self._recent_vic)
                        self._waiting = False
                        # Let the human know that the agent is coming over to help
                        self._send_message(
                            'Moving to ' + str(self._door['room_name']) + ' to help you remove an obstacle.',
                            'RescueBot')
                        # Plan the path to the relevant area
                        self._phase = Phase.PLAN_PATH_TO_ROOM
                    # Come over to help after dropping a victim that is currently being carried by the agent
                    else:
                        area = 'area ' + msg.split()[-1]
                        self._send_message('Will come to ' + area + ' after dropping ' + self._goal_vic + '.',
                                          'RescueBot')
            # Store the current location of the human in memory
            if mssgs and mssgs[-1].split()[-1] in ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13',
                                                   '14']:
                self._human_loc = int(mssgs[-1].split()[-1])

    def _loadBelief(self, members, folder):
        '''
        Loads trust belief values if agent already collaborated with human before, otherwise trust belief values are initialized using default values.
        '''
        # Create a dictionary with trust values for all team members
        trustBeliefs = {}
        # Set a default starting trust value
        default = 0.5

        # Check if agent already collaborated with this human before, if yes: load the corresponding trust values, if no: initialize using default trust values
        with open(folder + '/beliefs/allTrustBeliefs.json', 'r') as file:
            data = json.load(file)
            if self._human_name in data:
                return data[self._human_name]
            else:
                trustBeliefs[self._human_name] = {
                    task: {"competence": default, "willingness": default}
                    for task in self.TASKS
                }
                return trustBeliefs[self._human_name]

    '''
    Trust Model
    Each dictionary has actions (as keys) which determine whether the trust value increases or decreases depending on the paramaters in the sub-dictionaries (as values)
        - task: determines which task to update the trust values for
        - impact: determines the significance of the task (positive for awarding trust and negative for removing trust)
        - weight: determines the significance of each observed repetition of the task (should always be positive)
        - alpha (optional): scales the overall value of the trust (default is 0.05)
        - beta (optional): the exponent of the exponential function used to adjust trust (default is 0.15)
    '''
    COMPETENCE_MODEL = {
        "rescue_victim":                 {"task": "rescue", "impact": 1.0, "weight": 1.0},
        "help_carry_critical_victim":    {"task": "rescue", "impact": 0.8, "weight": 0.3},
        "carry_victim_alone":            {"task": "rescue", "impact": 0.6, "weight": 0.3}, # Award because RescueBot carrying alone is always slower
        "find_victim":                   {"task": "search", "impact": 0.5, "weight": 1.0},
        "remove_obstacle_together_near": {"task": "clear", "impact": 0.4, "weight": 0.5}, # Award oppertunistic choice
        "search_finished_rooms":         {"task": "search", "impact": -0.2, "weight": 0.7},
        "remove_obstacle_together_far":  {"task": "clear", "impact": -0.5, "weight": 0.6}, # Penalise for making robot waste time on traversal
        "prioritise_mild_over_critical": {"task": "rescue", "impact": -0.7, "weight": 0.8}
    }

    WILLINGNESS_MODEL = {
        "help_remove_obstacle":          {"task": "clear", "impact": 0.8, "weight": 0.9},
        "remove_obstacle":               {"task": "clear", "impact": 0.8, "weight": 0.6},
        "request_help_obstacle":         {"task": "clear", "impact": 0.6, "weight": 0.4},
        "search_room":                   {"task": "search", "impact": 0.5, "weight": 0.3},
        "carry_victim_intent":            {"task": "search", "impact": 0.4, "weight": 0.5},
        "search_room_intent":            {"task": "search", "impact": 0.3, "weight": 0.2},
        "idle":                          {"task": "search", "impact": -0.1, "weight": 0.1},
        "ignore":                        {"task": "clear", "impact": -0.2, "weight": 0.2},
        "ignore_rescue":                 {"task": "rescue", "impact": -0.4, "weight": 0.5},
        "lie_searching":                 {"task": "search", "impact": -0.5, "weight": 1.0},
        "lie_victim_found":              {"task": "search", "impact": -0.8, "weight": 1.0},
        "abandom_victim_transport":      {"task": "rescue", "impact": -1.0, "weight": 1.0},
    }

    def _trustBelief(self, members, trustBeliefs, folder, receivedMessages):
        """
        Updates trust belief values based on received messages.
        """
        # Dictionary to track positive and negative repetitions for each task
        task_repetitions = {task: {"positive": 0, "negative": 0} for task in self.TASKS}
    
        victim_types = [
        "critically injured girl",
        "critically injured elderly woman",
        "critically injured man",
        "critically injured dog",
        "mildly injured boy",
        "mildly injured elderly man",
        "mildly injured woman",
        "mildly injured cat"
        ]
        # --- Filter out messages containing "Our score is" ---
        current_filtered = [msg for msg in self.received_messages_content if "our score is" not in msg.lower()]

        # --- Check if a flush occurred ---
        if current_filtered:
            # If the first message in the new filtered array is different, a flush occurred.
            if self._stored_first_message is None or current_filtered[0] != self._stored_first_message:
                # A flush occurred: treat all messages in current_filtered as new.
                new_msgs = current_filtered
                self._all_messages.extend(new_msgs)
                # Reset the counter to the length of the new filtered array.
                self._last_processed_index = len(current_filtered)
                # Update the stored first message for future comparisons.
                self._stored_first_message = current_filtered[0]
            else:
                # No flush: append messages not processed yet (if any).
                new_msgs = current_filtered[self._last_processed_index:]
                if new_msgs:
                    self._all_messages.extend(new_msgs)
                    self._last_processed_index += len(new_msgs)
    
        for i, message in enumerate(self._all_messages):
            current_msg = message.lower()
            next_msg = self._all_messages[i+1].lower() if i+1 < len(self._all_messages) else None
            if next_msg != None:
                if 'found mildly injured' in current_msg and 'close' in current_msg and 'rescue together' in next_msg:
                    task_repetitions['rescue']['positive'] += 1
                    self._updateTrust(trustBeliefs, "carry_victim_intent", task_repetitions['rescue']['positive'])
                    for victim in victim_types:
                        if victim in current_msg and victim in self._collected_victims:
                            task_repetitions['rescue']['positive'] += 1
                            self._updateTrust(trustBeliefs, "rescue_victim", task_repetitions['rescue']['positive'])
                if 'found mildly injured' in current_msg and 'close' in current_msg and ('rescue alone' in next_msg or 'continue' in next_msg):
                    task_repetitions['rescue']['negative'] += 1
                    self._updateTrust(trustBeliefs, "ignore_rescue", task_repetitions['rescue']['negative'])

                if 'found critically injured' in current_msg and 'close' in current_msg and 'rescue' in next_msg:
                    task_repetitions['rescue']['positive'] += 1
                    self._updateTrust(trustBeliefs, "carry_victim_intent", task_repetitions['rescue']['positive'])
                    for victim in victim_types:
                        if victim in current_msg and victim in self._collected_victims:
                            task_repetitions['rescue']['positive'] += 1
                            self._updateTrust(trustBeliefs, "help_carry_critical_victim", task_repetitions['rescue']['positive'])
                if 'found critically injured' in current_msg and 'close' in current_msg and 'continue' in next_msg:
                    task_repetitions['rescue']['negative'] += 1
                    self._updateTrust(trustBeliefs, "ignore_rescue", task_repetitions['rescue']['negative'])

                if 'found rock' in current_msg and 'close' in current_msg and 'remove' in next_msg:
                    task_repetitions['clear']['positive'] += 1
                    self._updateTrust(trustBeliefs, "help_remove_obstacle", task_repetitions['clear']['positive'])
                    area_match = re.search(r'area\s*(\d+)', current_msg)
                    if area_match:
                        blocked_area = "area " + area_match.group(1)
                        # Check if the blocked area is in the list of searched rooms.
                        if blocked_area in self._searched_rooms:
                            task_repetitions['clear']['positive'] += 1
                            self._updateTrust(trustBeliefs, "remove_obstacle_together_near", task_repetitions['clear']['positive'])
                
                if 'found rock' in current_msg and 'close' in current_msg and 'continue' in next_msg:
                    task_repetitions['clear']['negative'] += 1
                    self._updateTrust(trustBeliefs, "ignore", task_repetitions['clear']['negative'])

                if 'found stones' in current_msg and 'close' in current_msg and 'remove together' in next_msg:
                    task_repetitions['clear']['positive'] += 1
                    self._updateTrust(trustBeliefs, "help_remove_obstacle", task_repetitions['clear']['positive'])
                    area_match = re.search(r'area\s*(\d+)', current_msg)
                    if area_match:
                        blocked_area = "area " + area_match.group(1)
                        # Check if the blocked area is in the list of searched rooms.
                        if blocked_area in self._searched_rooms:
                            task_repetitions['clear']['positive'] += 1
                            self._updateTrust(trustBeliefs, "remove_obstacle_together_near", task_repetitions['clear']['positive'])

                if 'found stones' in current_msg and 'close' in current_msg and ('remove alone' in next_msg or 'continue' in next_msg):
                    task_repetitions['clear']['negative'] += 1
                    self._updateTrust(trustBeliefs, "ignore", task_repetitions['clear']['negative'])


                if 'to help you remove an obstacle' in current_msg and 'removing tree' in next_msg:
                    task_repetitions['clear']['positive'] += 1
                    self._updateTrust(trustBeliefs, "request_help_obstacle", task_repetitions['clear']['positive'])
                
                if 'collect' in current_msg:
                    task_repetitions['rescue']['positive'] += 1
                    self._updateTrust(trustBeliefs, "carry_victim_intent", task_repetitions['rescue']['positive'])
                    for victim in victim_types:
                        if victim in current_msg and victim in self._collected_victims:
                            task_repetitions['rescue']['positive'] += 1
                            self._updateTrust(trustBeliefs, "carry_victim_alone", task_repetitions['rescue']['positive'])
                
                if 'i searched the whole area without finding' in current_msg:
                    task_repetitions['search']['negative'] += 1
                    self._updateTrust(trustBeliefs, "lie_victim_found", task_repetitions['search']['negative'])
                
                if 'waited' in current_msg:
                    seconds_match = re.search(r'(\d+)\s*seconds', current_msg)
                    if seconds_match:
                        seconds = int(seconds_match.group(1))
                        seconds = min(seconds, 30)
                        # For 30 seconds, impact is -0.3; scale linearly (e.g. 20 seconds gives -0.2).
                        impact = -(seconds / 30) * 0.3
                        if 'destroy obstacles' in current_msg or '':
                            task_repetitions['clear']['negative'] += 1
                            self._updateTrust(trustBeliefs, "ignore", task_repetitions['clear']['negative'])
                        elif 'rescue' in current_msg:
                            task_repetitions['rescue']['negative'] += 1
                            self._updateTrust(trustBeliefs, "abandom_victim_transport", task_repetitions['rescue']['negative'])
                
                if 'human said he searched room' in current_msg:
                    if 'critically injured' in current_msg:
                        task_repetitions['search']['negative'] += 1
                        self._updateTrust(trustBeliefs, "lie_searching", task_repetitions['search']['negative'])
                    elif 'mildly injured' in current_msg:
                        task_repetitions['search']['negative'] += 1
                        self._updateTrust(trustBeliefs, "lie_searching", task_repetitions['search']['negative'])
                    elif 'obstacle' in current_msg:
                        task_repetitions['search']['negative'] += 1
                        self._updateTrust(trustBeliefs, "lie_searching", task_repetitions['search']['negative'])

        # Save current trust belief values so we can later use and retrieve them to add to a json file with all the logged trust belief values
        with open(folder + '/beliefs/currentTrustBelief.json', 'w') as file:
            json.dump(trustBeliefs, file, indent=4)

        return trustBeliefs


    def _updateTrust(self, trustBeliefs, action, rep=1):
        '''
        Update the trust values for the given task, based on the given action which is quantified by the number of repetitions
        '''
        deltaCompetence = 0.0
        deltaWillingness = 0.0
        task = None
        print(rep)

        if action in self.COMPETENCE_MODEL:
            params = self.COMPETENCE_MODEL[action]
            task = params["task"]
            if task not in self.TASKS:
                print(f"ERROR: Action '{action}' references an invalid task '{task}'. Skipping update.")
                return

            x = params["impact"] * params["weight"] * rep
            alpha = params.get("alpha", 0.05)
            beta = params.get("beta", 0.15)
            deltaCompetence = self._exponential(x, alpha, beta)
            

        if action in self.WILLINGNESS_MODEL:
            params = self.WILLINGNESS_MODEL[action]
            task = params["task"]
            if task not in self.TASKS:
                print(f"ERROR: Action '{action}' references an invalid task '{task}'. Skipping update.")
                return

            x = params["impact"] * params["weight"] * rep
            alpha = params.get("alpha", 0.05)
            beta = params.get("beta", 0.15)
            deltaWillingness = self._exponential(x, alpha, beta)

        if action not in self.COMPETENCE_MODEL and action not in self.WILLINGNESS_MODEL:
            print(f"WARNING: updateTrust was called with an unknown action '{action}'. Skipping update.")
            return

        if deltaCompetence != 0.0:
            print("Observed the action", action, ": Competence changed from", trustBeliefs[task]['competence'], "to", trustBeliefs[task]['competence'] + deltaCompetence, "[ ", deltaCompetence, "]")

        if deltaWillingness != 0.0:
            print("Observed the action", action, ": Willingness changed from", trustBeliefs[task]['willingness'], "to", trustBeliefs[task]['willingness'] + deltaWillingness, "[ ", deltaWillingness, "]")

        # Calculate new competence value, clip between (-1,1), replace old competence value
        newCompetence = trustBeliefs[task]['competence'] + deltaCompetence
        trustBeliefs[task]['competence'] = np.clip(newCompetence, -1, 1)

        # Calculate new willingness value, clip between (-1,1), replace old willingness value
        newWillingness = trustBeliefs[task]['willingness'] + deltaWillingness
        trustBeliefs[task]['willingness'] = np.clip(newWillingness, -1, 1)

    def _exponential(self, x, alpha=0.05, beta=0.15):
        '''
        Implements exponential growth (positive x values) and exponential decay (negative x values): https://en.wikipedia.org/wiki/Exponential_decay
        '''
        if x >= 0:
            return alpha * np.exp(beta * x)
        else:
            return -alpha * np.exp(beta * abs(x))

    def _logistic(self, x, L, k, x0):
        '''
        Implements the mathematical logistic function: https://en.wikipedia.org/wiki/Logistic_function
        '''
        return L / (1 + np.exp(-k * (x - x0)))

    def _send_message(self, mssg, sender):
        '''
        send messages from agent to other team members
        '''
        msg = Message(content=mssg, from_id=sender)
        if msg.content not in self.received_messages_content and 'Our score is' not in msg.content:
            self.send_message(msg)
            self._send_messages.append(msg.content)
        # Sending the hidden score message (DO NOT REMOVE)
        if 'Our score is' in msg.content:
            self.send_message(msg)

    def _getClosestRoom(self, state, objs, currentDoor):
        '''
        calculate which area is closest to the agent's location
        '''
        agent_location = state[self.agent_id]['location']
        locs = {}
        for obj in objs:
            locs[obj] = state.get_room_doors(obj)[0]['location']
        dists = {}
        for room, loc in locs.items():
            if currentDoor != None:
                dists[room] = utils.get_distance(currentDoor, loc)
            if currentDoor == None:
                dists[room] = utils.get_distance(agent_location, loc)

        return min(dists, key=dists.get)

    def _efficientSearch(self, tiles):
        '''
        efficiently transverse areas instead of moving over every single area tile
        '''
        x = []
        y = []
        for i in tiles:
            if i[0] not in x:
                x.append(i[0])
            if i[1] not in y:
                y.append(i[1])
        locs = []
        for i in range(len(x)):
            if i % 2 == 0:
                locs.append((x[i], min(y)))
            else:
                locs.append((x[i], max(y)))
        return locs
