#!/usr/bin/env python
"""
Created on Thu Nov 21 10:30:22 2019

@author: Adam Binch (abinch@sagarobotics.com)
"""
#####################################################################################
import rospy, rosservice, rostopic, actionlib, subprocess
from threading import Lock


class Executor(object):
    
    
    def __init__(self, config, lock_exec, event_cb):

        self.config = config
        self.lock_exec = lock_exec
        self.event_cb = event_cb
        
        self._lock = Lock()
        self.processes = []
        
        for process in config:
            
            process_type = process.keys()[0]
            print "Initialising process of type {}".format("\033[1m"+process_type+"\033[0m")
            
            if process_type == "call":
                self.init_call(process)
                
            elif process_type == "publish":
                self.init_publish(process)
                
            elif process_type == "action":
                self.init_action(process)
                
            elif process_type == "sleep":
                self.init_sleep(process)
                
            elif process_type == "shell":
                self.init_shell(process)
                
            else:
                rospy.logerr("Process of type {} not supported".format("\033[1m"+process_type+"\033[0m"))
                
        print "\n"
                    
                    
    def init_call(self, process):
        
        try:
            service_name = process["call"]["service_name"]
            service_class = rosservice.get_service_class_by_name(service_name)

            rospy.wait_for_service(service_name, timeout=5.0)
            service_client = rospy.ServiceProxy(service_name, service_class)
            
            req = service_class._request_class()
            for arg in process["call"]["service_args"]: exec(arg)

            d = {}
            d["message"] = self.get_message(process["call"])
            d["default_msg"] = " Calling service '{}'.".format(service_name)
            d["func"] = "self.call(**kwargs)"
            d["kwargs"] = {}
            d["kwargs"]["service_client"] = service_client
            d["kwargs"]["req"] = req
            
            self.processes.append(d)
            
        except Exception as e:
            rospy.logerr(e)
            
            
    def init_publish(self, process):
        
        try:
            topic_name = process["publish"]["topic_name"]
            topic_latched = process["publish"]["topic_latched"]
            
            msg_class, real_topic, _ = rostopic.get_topic_class(topic_name)
            pub = rospy.Publisher(real_topic, msg_class, latch=topic_latched, 
                                  queue_size=10)
            
            msg = msg_class()
            for arg in process["publish"]["topic_args"]: exec(arg)
                
            d = {}
            d["message"] = self.get_message(process["publish"])
            d["default_msg"] = " Publishing to topic '{}'.".format(topic_name)
            d["func"] = "self.publish(**kwargs)"
            d["kwargs"] = {}
            d["kwargs"]["pub"] = pub
            d["kwargs"]["msg"] = msg
            
            self.processes.append(d)
            
        except Exception as e:
            rospy.logerr(e)
            
            
    def init_action(self, process):
        
        try:
            namespace = process["action"]["namespace"]
            package = process["action"]["package"]
            spec = process["action"]["action_spec"]
            
            exec("from {}.msg import {} as action_spec".format(package, spec))
            exec("from {}.msg import {} as goal_class".format(package, spec[:-6] + "Goal"))
            
            action_client = actionlib.SimpleActionClient(namespace, action_spec)
            wait = action_client.wait_for_server(rospy.Duration(5.0))
            if not wait:
                rospy.logerr("Action server with namespace '{}' and action spec '{}' not available.".format(namespace, spec))
                return
    
            goal = goal_class()
            for arg in process["action"]["goal_args"]: exec(arg)
                
            d = {}
            d["message"] = self.get_message(process["action"])
            d["default_msg"] = " Sending goal for action with spec '{}'.".format(spec)
            d["func"] = "self.action(**kwargs)"
            d["kwargs"] = {}
            d["kwargs"]["action_client"] = action_client
            d["kwargs"]["goal"] = goal
            
            self.processes.append(d)
        
        except Exception as e:
            rospy.logerr(e)
            
        
    def init_sleep(self, process):
        
        try:
            d = {}
            d["message"] = self.get_message(process["sleep"])
            d["default_msg"] = " Sentor sleeping for {} seconds.".format(process["sleep"]["duration"])
            d["func"] = "self.sleep(**kwargs)"
            d["kwargs"] = {}
            d["kwargs"]["duration"] = process["sleep"]["duration"]
            
            self.processes.append(d)

        except Exception as e:
            rospy.logerr(e)
            
            
    def init_shell(self, process):
        
        try:
            d = {}
            d["message"] = self.get_message(process["shell"])
            d["default_msg"] = " Executing shell commands {}.".format(process["shell"]["cmd_args"])
            d["func"] = "self.shell(**kwargs)"
            d["kwargs"] = {}
            d["kwargs"]["cmd_args"] = process["shell"]["cmd_args"]
            
            self.processes.append(d)

        except Exception as e:
            rospy.logerr(e)
            
            
    def get_message(self, process):
        
        if "message" in process.keys():
            message = process["message"]
        else:
            message = ""
        
        return message
        
        
    def execute(self):
        
        if self.lock_exec:
            self._lock.acquire()
        
        for process in self.processes:
            rospy.sleep(0.1) # needed when using slackeros
            try:
                self.event_cb(process["message"] + process["default_msg"], "info")
                kwargs = process["kwargs"]            
                eval(process["func"])
                
            except Exception as e:
                self.event_cb(str(e), "error")
            
        if self.lock_exec:
            self._lock.release()
            

    def call(self, service_client, req):
        
        resp = service_client(req)
        
        if resp.success:
            self.event_cb("Service call success: {}".format(resp.success), "info")
        else:
            self.event_cb("Service call success: {}".format(resp.success), "error")
        
        
    def publish(self, pub, msg):
        pub.publish(msg)
        
        
    def action(self, action_client, goal):
        action_client.send_goal(goal, self.goal_cb)
        
       
    def sleep(self, duration):
        rospy.sleep(duration)
        
        
    def shell(self, cmd_args):
        
        process = subprocess.Popen(cmd_args,
                     stdout=subprocess.PIPE, 
                     stderr=subprocess.PIPE)
                     
        stdout, stderr = process.communicate()
        print stdout
        print stderr
        
        
    def goal_cb(self, status, result):
        
        if status == 3:
            self.event_cb("Goal achieved", "info")
        elif status == 2 or status == 6:
            self.event_cb("Goal preempted", "warn")
        else:
            self.event_cb("Goal failed", "error")
#####################################################################################