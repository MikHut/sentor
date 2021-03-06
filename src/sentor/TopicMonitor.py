#!/usr/bin/env python
"""
@author: Francesco Del Duchetto (FDelDuchetto@lincoln.ac.uk)
@author: Adam Binch (abinch@sagarobotics.com)
"""
#####################################################################################
from sentor.ROSTopicHz import ROSTopicHz
from sentor.ROSTopicFilter import ROSTopicFilter
from sentor.ROSTopicPub import ROSTopicPub
from sentor.Executor import Executor

from threading import Thread, Event, Lock
import socket
import rostopic
import rosgraph
import rospy
import time
import subprocess
import os

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
##########################################################################################    


##########################################################################################
class TopicMonitor(Thread):


    def __init__(self, topic_name, rate, signal_when_config, signal_lambdas_config, processes, 
                 timeout, default_notifications, event_callback, thread_num):
        Thread.__init__(self)

        self.topic_name = topic_name
        self.rate = rate
        self.signal_when_config = signal_when_config
        self.signal_lambdas_config = signal_lambdas_config
        self.processes = processes
        if timeout > 0:
            self.timeout = timeout
        else:
            self.timeout = 0.1
        self.default_notifications = default_notifications
        self._event_callback = event_callback
        self.thread_num = thread_num
        
        self.nodes = []
        self.sat_crit_expressions = []
        self.sat_expressions_timer = {}
        self.sat_expr_repeat_timer = {}
        self.crit_conditions = {}
        
        self.process_signal_config()
        
        self._stop_event = Event()
        self._killed_event = Event()
        self._lock = Lock()
        
        self.pub_monitor = None
        self.hz_monitor = None
        self.is_topic_published = True 
        self.is_instantiated = False
        self.is_instantiated = self._instantiate_monitors()
        
        self.signal_when_is_safe = True
        self.lambdas_are_safe = True
        self.thread_is_safe = True
        
        if processes:
            self.executor = Executor(processes, self.event_callback)


    def _instantiate_monitors(self):
        if self.is_instantiated: return True

        try:
            msg_class, real_topic, _ = rostopic.get_topic_class(self.topic_name, blocking=False)
            topic_type, _, _ = rostopic.get_topic_type(self.topic_name, blocking=False)
        except rostopic.ROSTopicException as e:
            self.event_callback("Topic %s type cannot be determined, or ROS master cannot be contacted" % self.topic_name, "warn")
            return False

        if real_topic is None:
            self.event_callback("Topic %s is not published" % self.topic_name, "warn")
            if self.signal_when.lower() == 'not published' and self.safety_critical:
                self.signal_when_is_safe = False
            return False
        
        # if rate > 0 set in config then throttle topic at that rate
        if self.rate > 0:
            COMMAND_BASE = ["rosrun", "topic_tools", "throttle"]
            subscribed_topic = "/sentor/monitoring/" + str(self.thread_num) + real_topic
            
            command = COMMAND_BASE + ["messages", real_topic, str(self.rate), subscribed_topic]
            subprocess.Popen(command, stdout=open(os.devnull, "wb"))
        else:
            subscribed_topic = real_topic

        # find out topic publishing nodes
        master = rosgraph.Master(rospy.get_name())
        try:
            pubs, _ = rostopic.get_topic_list(master=master)
            # filter based on topic
            pubs = [x for x in pubs if x[0] == real_topic]
            nodes = []
            for _, _, _nodes in pubs:
                nodes += _nodes
            self.nodes = nodes
        except socket.error:
            self.event_callback("Could not retrieve nodes for topic %s" % self.topic_name, "warn")

        # Do we need a hz monitor?
        hz_monitor_required = False
        if self.signal_when.lower() == 'not published':
            hz_monitor_required = True
        for signal_lambda in self.signal_lambdas_config:
             if "when_published" in signal_lambda:
                 if signal_lambda["when_published"]:
                     hz_monitor_required = True
        
        if hz_monitor_required:
            self.hz_monitor = self._instantiate_hz_monitor(subscribed_topic, self.topic_name, msg_class)

        if self.signal_when.lower() == 'published':
            print "Signaling 'published' for "+ bcolors.OKBLUE + self.topic_name + bcolors.ENDC +" initialized"
            self.pub_monitor = self._instantiate_pub_monitor(subscribed_topic, self.topic_name, msg_class)
            self.pub_monitor.register_published_cb(self.published_cb)
            
            if self.safety_critical:
                self.signal_when_is_safe = False

        elif self.signal_when.lower() == 'not published':
            print "Signaling 'not published' for "+ bcolors.BOLD + str(self.signal_when_timeout) + " seconds" + bcolors.ENDC +" for " + bcolors.OKBLUE + self.topic_name + bcolors.ENDC +" initialized"

        if len(self.signal_lambdas_config):
            print "Signaling expressions for "+ bcolors.OKBLUE + self.topic_name + bcolors.ENDC + ":"
            
            self.lambda_monitor_list = []
            for signal_lambda in self.signal_lambdas_config:
                
                lambda_fn_str = signal_lambda["expression"]
                lambda_config = self.process_lambda_config(signal_lambda)
                
                if lambda_fn_str != "":
                    print "\t" + bcolors.OKGREEN + lambda_fn_str + bcolors.ENDC + " ("+ bcolors.BOLD+"timeout: %s seconds" %  lambda_config["timeout"] + bcolors.ENDC +")"
                    lambda_monitor = self._instantiate_lambda_monitor(subscribed_topic, msg_class, lambda_fn_str, lambda_config)

                    # register cb that notifies when the lambda function is True
                    lambda_monitor.register_satisfied_cb(self.lambda_satisfied_cb)
                    lambda_monitor.register_unsatisfied_cb(self.lambda_unsatisfied_cb)

                    self.lambda_monitor_list.append(lambda_monitor)
            print ""

        self.is_instantiated = True

        return True
    

    def event_callback(self, string, type, msg=""):
        self._event_callback(string, type, msg, self.nodes, self.topic_name)
        
        
    def process_signal_config(self):
        
        self.signal_when = ""
        self.signal_when_timeout = self.timeout
        self.safety_critical = False
        self.signal_when_def_nots = self.default_notifications
        self.process_indices = None
        self.repeat_exec = False
        self.tags = []
        
        if type(self.signal_when_config) is str:
            self.signal_when = self.signal_when_config
        elif type(self.signal_when_config) is dict:
        
            if "condition" in self.signal_when_config:
                self.signal_when = self.signal_when_config["condition"]
            if "timeout" in self.signal_when_config:
                self.signal_when_timeout = self.signal_when_config["timeout"]
            if "safety_critical" in self.signal_when_config:
                self.safety_critical = self.signal_when_config["safety_critical"]
            if "default_notifications" in self.signal_when_config:
                self.signal_when_def_nots = self.signal_when_config["default_notifications"]
            if "process_indices" in self.signal_when_config:
                self.process_indices = self.signal_when_config["process_indices"]
            if "repeat_exec" in self.signal_when_config:
                self.repeat_exec = self.signal_when_config["repeat_exec"]
            if "tags" in self.signal_when_config:
                self.tags = self.signal_when_config["tags"]
            
        if self.signal_when_timeout <= 0:
            self.signal_when_timeout = 0.1
        
        # for publishing list of safety critical conditions
        if self.safety_critical:
            d = {}
            d["safe"] = True
            d["tags"] = self.tags
            self.crit_conditions[self.signal_when] = d
            
        
    def process_lambda_config(self, signal_lambda):

        lambda_config = {}
        lambda_config["expr"] = ""
        lambda_config["file"] = None
        lambda_config["package"] = None
        lambda_config["timeout"] = self.timeout
        lambda_config["safety_critical"] = False
        lambda_config["default_notifications"] = self.default_notifications
        lambda_config["when_published"] = False
        lambda_config["process_indices"] = None
        lambda_config["repeat_exec"] = False
        lambda_config["tags"] = []
        
        if "expression" in signal_lambda:
            lambda_config["expr"] = signal_lambda["expression"]
        if "file" in signal_lambda:
            lambda_config["file"] = signal_lambda["file"]
        if "package" in signal_lambda:
            lambda_config["package"] = signal_lambda["package"]
        if "timeout" in signal_lambda:
            lambda_config["timeout"] = signal_lambda["timeout"]
        if "safety_critical" in signal_lambda:
            lambda_config["safety_critical"] = signal_lambda["safety_critical"]                
        if "default_notifications" in signal_lambda:
            lambda_config["default_notifications"] = signal_lambda["default_notifications"]    
        if "when_published" in signal_lambda:
            lambda_config["when_published"] = signal_lambda["when_published"]
        if "process_indices" in signal_lambda:
            lambda_config["process_indices"] = signal_lambda["process_indices"]
        if "repeat_exec" in signal_lambda:
            lambda_config["repeat_exec"] = signal_lambda["repeat_exec"]      
        if "tags" in signal_lambda:
            lambda_config["tags"] = signal_lambda["tags"]      
            
        if lambda_config["timeout"] <= 0:
            lambda_config["timeout"] = 0.1
        
        # for publishing list of safety critical conditions
        if lambda_config["safety_critical"]:
            d = {}
            d["safe"] = True
            d["tags"] = lambda_config["tags"]
            self.crit_conditions[lambda_config["expr"]] = d
            
        return lambda_config
        

    def _instantiate_hz_monitor(self, subscribed_topic, topic_name, msg_class):
        hz = ROSTopicHz(topic_name, 1000)

        rospy.Subscriber(subscribed_topic, msg_class, hz.callback_hz)

        return hz
        

    def _instantiate_pub_monitor(self, subscribed_topic, topic_name, msg_class):
        pub = ROSTopicPub(topic_name)

        rospy.Subscriber(subscribed_topic, msg_class, pub.callback_pub)

        return pub
        

    def _instantiate_lambda_monitor(self, subscribed_topic, msg_class, lambda_fn_str, lambda_config):
        filter = ROSTopicFilter(self.topic_name, lambda_fn_str, lambda_config)

        rospy.Subscriber(subscribed_topic, msg_class, filter.callback_filter)

        return filter
        

    def run(self):
        # if the topic was not published initially then no monitor is running
        # but, maybe now it is published
        if not self.is_instantiated:
            if not self._instantiate_monitors():
                return
            else:
                self.is_instantiated = True
                
        def cb(_):
            if self.signal_when.lower() == 'not published':
                if self.safety_critical:
                    self.signal_when_is_safe = False
                    self.crit_conditions[self.signal_when]["safe"] = False
                if self.signal_when_def_nots and self.safety_critical:
                    self.event_callback("SAFETY CRITICAL: Topic %s is not published anymore" % self.topic_name, "error")
                elif self.signal_when_def_nots:
                    self.event_callback("Topic %s is not published anymore" % self.topic_name, "warn")
                if not self.repeat_exec:
                    self.execute(process_indices=self.process_indices)

        def repeat_cb(_):
            if self.signal_when.lower() == 'not published':
                self.execute(process_indices=self.process_indices)

        timer = None
        timer_repeat = None
        while not self._killed_event.isSet():
            while not self._stop_event.isSet():
                
                self.thread_is_safe = self.signal_when_is_safe and self.lambdas_are_safe
                
                # check it is still published (None if not)
                if self.hz_monitor is not None:
                    rate = self.hz_monitor.get_hz()
                    
                    if rate is None and self.is_topic_published:
                        self.is_topic_published = False
    
                        timer = rospy.Timer(rospy.Duration.from_sec(self.signal_when_timeout), cb, oneshot=True)
                        
                        if self.repeat_exec:
                            timer_repeat = rospy.Timer(rospy.Duration.from_sec(self.signal_when_timeout), repeat_cb, oneshot=False)
    
                    if rate is not None:
                        self.is_topic_published = True
                        
                        if self.safety_critical:
                            self.signal_when_is_safe = True
                            self.crit_conditions[self.signal_when]["safe"] = True
    
                        if timer is not None:
                            timer.shutdown()
                            timer = None
                        
                        if self.repeat_exec:
                            if timer_repeat is not None:
                                timer_repeat.shutdown()
                                timer_repeat = None

                time.sleep(0.3)
            time.sleep(1)
            

    def lambda_satisfied_cb(self, expr, msg, config):
        
        def ProcessLambda(timer_dict):
            process_lambda = True
            if config["when_published"] and not self.is_topic_published:
                process_lambda = False
                timer_dict = self.kill_timer(timer_dict, config["expr"]) 
            return process_lambda, timer_dict
            
        if not self._stop_event.isSet():    
            if not expr in self.sat_expressions_timer:
                
                def cb(_):
                    process_lambda, self.sat_expressions_timer = ProcessLambda(self.sat_expressions_timer)
                    if process_lambda:
                        if config["safety_critical"]:
                            self.lambdas_are_safe = False
                            self.sat_crit_expressions.append(config["expr"])
                            self.crit_conditions[config["expr"]]["safe"] = False
                        
                        if config["default_notifications"]:
                            if config["safety_critical"]:
                                self.event_callback("SAFETY CRITICAL: Expression '%s' for %s seconds on topic %s satisfied" % (expr, config["timeout"], self.topic_name), "error", msg)
                            else:
                                self.event_callback("Expression '%s' for %s seconds on topic %s satisfied" % (expr, config["timeout"], self.topic_name), "warn", msg)
                        
                        if not config["repeat_exec"]:
                            self.execute(msg, config["process_indices"])
                
                self._lock.acquire()
                self.sat_expressions_timer.update({expr: rospy.Timer(rospy.Duration.from_sec(config["timeout"]), cb, oneshot=True)})
                self._lock.release()
            
            if config["repeat_exec"]:
                if not expr in self.sat_expr_repeat_timer:
                    
                    def repeat_cb(_):
                        process_lambda, self.sat_expr_repeat_timer = ProcessLambda(self.sat_expr_repeat_timer)
                        if process_lambda:     
                            self.execute(msg, config["process_indices"])
                            self.sat_expr_repeat_timer = self.kill_timer(self.sat_expr_repeat_timer, config["expr"]) 
                            
                    self._lock.acquire()
                    self.sat_expr_repeat_timer.update({expr: rospy.Timer(rospy.Duration.from_sec(config["timeout"]), repeat_cb, oneshot=True)})
                    self._lock.release()  
                    

    def lambda_unsatisfied_cb(self, expr):
        if not self._stop_event.isSet():            
            if expr in self.sat_expressions_timer:
                self.sat_expressions_timer = self.kill_timer(self.sat_expressions_timer, expr) 
                
            if expr in self.sat_expr_repeat_timer:
                self.sat_expr_repeat_timer = self.kill_timer(self.sat_expr_repeat_timer, expr) 
                
            if expr in self.sat_crit_expressions:
                self.sat_crit_expressions.remove(expr)
                self.crit_conditions[expr]["safe"] = True
                
            if not self.sat_crit_expressions:
                self.lambdas_are_safe = True


    def published_cb(self, msg):
        if not self._stop_event.isSet():
            if self.safety_critical:
                self.signal_when_is_safe = False
                self.crit_conditions[self.signal_when]["safe"] = False
            if self.default_notifications and self.safety_critical:
                self.event_callback("SAFETY CRITICAL: Topic %s is published " % (self.topic_name), "error")
            elif self.default_notifications:
                self.event_callback("Topic %s is published " % (self.topic_name), "warn")
            #self.execute(msg, self.process_indices)
                
                
    def kill_timer(self, timer_dict, expr):
        self._lock.acquire()
        timer_dict[expr].shutdown()
        timer_dict.pop(expr)
        self._lock.release()
        return timer_dict
            
            
    def execute(self, msg=None, process_indices=None):
        if self.processes:
            rospy.sleep(0.1) # needed when using slackeros
            self.executor.execute(msg, process_indices)
            
            
    def stop_monitor(self):
        self._stop_event.set()
        

    def start_monitor(self):
        self._stop_event.clear()
        

    def kill_monitor(self):
        self.stop_monitor()
        self._killed_event.set()
##########################################################################################