#!/usr/bin/env python
from sentor.TopicMonitor import TopicMonitor
from sentor.SafetyMonitor import SafetyMonitor
from std_msgs.msg import String
from std_srvs.srv import Empty
import pprint
import signal
import rospy
import time
import yaml
import sys
import os

# TODO nice printing of frequency of the topic with curses
# TODO consider timeout

unpublished_topics_indexes = []
satisfied_filters_indexes = []

topic_monitors = []

event_pub = None

def __signal_handler(signum, frame):
    def kill_monitors():
        for topic_monitor in topic_monitors:
            topic_monitor.kill_monitor()
    def join_monitors():
        for topic_monitor in topic_monitors:
            topic_monitor.join()
    kill_monitors()
    join_monitors()
    print "stopped."
    os._exit(signal.SIGTERM)

def stop_monitoring(_):
    for topic_monitor in topic_monitors:
        topic_monitor.stop_monitor()

    rospy.logwarn("sentor_node stopped monitoring")
    return

def start_monitoring(_):
    for topic_monitor in topic_monitors:
        topic_monitor.start_monitor()

    rospy.logwarn("sentor_node started monitoring")
    return

def event_callback(string, type, msg=""):
    if type == "info":
        rospy.loginfo(string + '\n' + str(msg))
    elif type == "warn":
        rospy.logwarn(string + '\n' + str(msg))
    elif type == "error":
        rospy.logerr(string + '\n' + str(msg))

    if event_pub is not None:
        event_pub.publish(String("%s: %s" % (type, string)))


if __name__ == "__main__":
    signal.signal(signal.SIGINT, __signal_handler)
    rospy.init_node("sentor")

    config_file = rospy.get_param("~config_file", "")

    try:
        topics = yaml.load(file(config_file, 'r'))
    except Exception as e:
        rospy.logerr("No configuration file provided: %s" % e)
        topics = []
    # else:
    #     pprint.pprint(topics)

    stop_srv = rospy.Service('/sentor/stop_monitor', Empty, stop_monitoring)
    start_srv = rospy.Service('/sentor/start_monitor', Empty, start_monitoring)

    event_pub = rospy.Publisher('/sentor/event', String, queue_size=10)
    custom_msg_pub = rospy.Publisher('/sentor/custom_msg', String, queue_size=10)
    
    safety_pub_rate = rospy.get_param("~safety_pub_rate", "")
    safety_monitor = SafetyMonitor(safety_pub_rate)

    topic_monitors = []
    print "Monitoring topics:"
    for topic in topics:
        try:
            topic_name = topic["name"]
        except Exception as e:
            rospy.logerr("topic name is not specified for entry %s" % topic)
            continue

        signal_when = ''
        safety_critical = False
        signal_lambdas = []
        processes = []
        lock_exec = False
        timeout = 0
        include = True
        if 'signal_when' in topic.keys():
            signal_when = topic['signal_when']
        if 'safety_critical' in topic.keys():
            safety_critical = topic['safety_critical']
        if 'signal_lambdas' in topic.keys():
            signal_lambdas = topic['signal_lambdas']
        if 'execute' in topic.keys():
            processes = topic['execute']
        if 'lock_exec' in topic.keys():
            lock_exec = topic['lock_exec']
        if 'timeout' in topic.keys():
            timeout = topic['timeout']
        if 'include' in topic.keys():
            include = topic['include']

        if include:
            topic_monitor = TopicMonitor(topic_name, signal_when, safety_critical, 
                                         signal_lambdas, processes, lock_exec, timeout, 
                                         event_callback, safety_monitor.safety_callback)
            topic_monitors.append(topic_monitor)

    time.sleep(1)

    # start monitoring
    for topic_monitor in topic_monitors:
        topic_monitor.start()

    rospy.spin()
