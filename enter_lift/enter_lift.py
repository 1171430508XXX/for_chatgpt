2454057744@qq.com
2 / 2

帮我修改一下代码，把print改成logging模块的输出，级别全使用info：
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_system_default
from rclpy.qos import QoSProfile
from rclpy.qos import QoSHistoryPolicy as History
from rclpy.qos import QoSDurabilityPolicy as Durability
from rclpy.qos import QoSReliabilityPolicy as Reliability
from rmf_lift_msgs.msg import EnterLiftError , LiftSignal ,LiftRequest ,LiftState
from rmf_fleet_msgs.msg import RobotState, Location, PathRequest
from rmf_task_msgs.msg import ApiRequest , ApiResponse
import math
import time 
import threading
from sys import  stdout
import json
import uuid
import re

class RobotsEnterLift(Node):
    def __init__(self):
        super().__init__('robots_enter_lift')
        transient_qos = QoSProfile(
          history=History.KEEP_LAST,
          depth=1,
          reliability=Reliability.RELIABLE,
          durability=Durability.TRANSIENT_LOCAL)
        
        self.task_publisher = self.create_publisher(ApiRequest,"/task_api_requests",transient_qos)
        self.task_subscription = self.create_subscription(
            ApiResponse, 'task_api_responses', self.task_response_callback, 10
        )

        self.path_publisher = self.create_publisher(
            PathRequest,
            'robot_path_requests',
            qos_profile = qos_profile_system_default)
        
        self.robot_state_subscription = self.create_subscription(
            RobotState,
            'robot_state',
            self.obtain_robot_callback,
            10
        )
               
        self.my_subscription = self.create_subscription(
            EnterLiftError,
            'enter_lift_error',
            self.my_callback,
            10
        )

        self.lift_signal_publisher = self.create_publisher(
            LiftSignal,
            'lift_signal',
            qos_profile = qos_profile_system_default)

        self.lift_request_publisher = self.create_publisher(
            LiftRequest,
            'adapter_lift_requests',
            qos_profile = qos_profile_system_default)
        
        self.lift_state_subscription = self.create_subscription(
            LiftState,
            'lift_states',
            self.confirm_lift,
            10)

        # 
        self.task_dict={} # 记录task 用my_request_id作为键 , 添加stage 标志该阻塞处于第几阶段 0-收到阻塞请求，1-发送中断请求，2-请求机器人撤回原位，3-重新呼叫电梯，4-发送恢复任务
        self.robots_dict={} # 记录机器人目标点target，以及当前my_request_id 用robot_name作为键
        self.lift_dict={} # 记录电梯使用情况  用lift_name作为键
        self._mutex = threading.Lock()

    def my_callback(self, msg):
        # [FIXME]
        # 触发中断任务
        # 并记录所需信息，request_id,lift_name,state,task_id,fleet_name,robot_name,location,(tokens)
        with self._mutex:
            my_request_id = str(msg.request_id)
            if my_request_id  not in self.task_dict:
                if msg.state == "BLOCK":
                    print(f"收到机器人{msg.robot_name}新的阻塞请求{my_request_id}")
                    # 保存必要信息到task_dict
                    task_info = {}
                    task_info["state"] = msg.state
                    task_info["lift_name"] = msg.lift_name
                    task_info["task_id"] = msg.task_id
                    task_info["fleet_name"]= msg.fleet_name
                    task_info["robot_name"] = msg.robot_name
                    #保存location
                    target_location={
                        't': msg.location.t,
                        'x': msg.location.x,
                        'y': msg.location.y,
                        'yaw': msg.location.yaw,
                        'obey_approach_speed_limit': msg.location.obey_approach_speed_limit,
                        'approach_speed_limit': msg.location.approach_speed_limit,
                        'level_name': msg.location.level_name,
                    }
                    task_info["location"] = target_location
                    task_info["tokens"] = []
                    task_info["stage"] = 0 # 收到阻塞请求
                    self.task_dict[my_request_id]=task_info
                    # 保存必要信息到robots_dict
                    target_info = {}
                    target_info["my_request_id"] = my_request_id
                    target_info["location"] = target_location
                    self.robots_dict[str(msg.robot_name)] = target_info
                    # 保存必要信息到lift_dict
                    lift_info = {}
                    lift_info["my_request_id"] = my_request_id
                    self.lift_dict[str(msg.lift_name)] = lift_info
                    # 发送中断任务请求
                    self.send_interrupt_request(my_request_id)

    def send_interrupt_request(self,my_request_id):
        task_id =self.task_dict[my_request_id]["task_id"]
        _json_msg = {
            "type" : "interrupt_task_request",
            "task_id":task_id,
        }
        json.dump(_json_msg,stdout)
        interrupt_request_id = "interrupt_"+my_request_id
        self.task_publisher.publish(ApiRequest(
            json_msg = json.dumps(_json_msg),
            request_id = interrupt_request_id
        ))
        print(f"发送中断任务请求{my_request_id}")

        self.task_dict[my_request_id]["stage"] = 1 #发送中断请求

    
    def send_resume_request(self,my_request_id):
        task_id =self.task_dict[my_request_id]["task_id"]
        tokens = self.task_dict[my_request_id]["tokens"]
        _json_msg = {
            "type" : "resume_task_request",
            "for_task":task_id,
            "for_tokens":tokens

        }
        json.dump(_json_msg,stdout)
        resume_request_id = "resume_"+my_request_id
        self.task_publisher.publish(ApiRequest(
            json_msg = json.dumps(_json_msg),
            request_id = resume_request_id
        ))
        print(f"发送恢复任务请求{my_request_id}")
        self.task_dict[my_request_id]["stage"] = 4 #发送恢复请求
    
    def send_robot_location(self,my_request_id):
        # 从电梯处获取，机器人应该退回的位置Location
        # task_id是当前中断任务的id
        # fleet_name与robot_name
        task_id = self.task_dict[my_request_id]["task_id"]
        fleet_name = self.task_dict[my_request_id]["fleet_name"]
        robot_name = self.task_dict[my_request_id]["robot_name"]
        print(f"请求机器人{robot_name}退回原位{my_request_id}")
        path_request = PathRequest()
        target_loc = Location()
        target_loc.t = self.get_clock().now().to_msg()
        target_loc.x = self.task_dict[my_request_id]["location"]["x"]
        target_loc.y = self.task_dict[my_request_id]["location"]["y"]
        target_loc.yaw = self.task_dict[my_request_id]["location"]["yaw"]
        target_loc.level_name = self.task_dict[my_request_id]["location"]["level_name"]
        if "target_speed_limit" in self.task_dict[my_request_id]["location"] and \
            self.task_dict[my_request_id]["location"]["target_speed_limit"] > 0:
            target_loc.obey_approach_speed_limit = True
            target_loc.approach_speed_limit = self.task_dict[my_request_id]["location"]["target_speed_limit"]

        target_loc2 = target_loc
        target_loc2.t = self.get_clock().now().to_msg()

        # 发一个path，包含两个点
        path_request.path.append(target_loc)
        path_request.path.append(target_loc2)

        path_request.fleet_name = fleet_name
        path_request.robot_name = robot_name
        path_request.task_id = str(task_id)
        # 一个path发两次
        self.path_publisher.publish(path_request)
        time.sleep(1)
        self.path_publisher.publish(path_request)
        self.task_dict[my_request_id]["stage"] = 2 #发送机器人撤回原位

    def obtain_robot_callback(self, msg):
        # 不断获取机器人当前位置，
        # 当机器人当前位置与目标位置相同时，认为该机器人已到达目标点
        # 从self.robots_dict字典中获取该机器人目前所对应的my_request_id,并执行send_resume_request
        # 更新self.robots_dict

        def is_close(a, b):
            return math.isclose(a, b, rel_tol=0.1)

        def has_arrived(robot):
            # 判断机器人已撤回到目标点
            with self._mutex:
                my_request_id = self.robots_dict[robot]["my_request_id"]
                target_location = self.robots_dict[robot]["location"]
                # if self.task_dict[my_request_id]["stage"]==2 :
                if self.task_dict[my_request_id]["stage"]==2 and \
                    is_close(target_location["x"],location_.x) and \
                        is_close(target_location["y"],location_.y) and \
                            is_close(target_location["yaw"],location_.yaw) and \
                                target_location["level_name"] ==  location_.level_name : #必须是机器人正在撤回原位状态
                    return True
                else :
                    return False
            
        # 说明是被追踪的robot
        robot = msg.name
        if robot in self.robots_dict:
            location_ = msg.location
            if has_arrived(robot=robot) :
                my_request_id = self.robots_dict[robot]["my_request_id"]
                print(f"机器人{robot}已退回原位{my_request_id}")
                # 让电梯先走 并 重新呼叫
                self.call_lift(my_request_id)
                del self.robots_dict[robot]


    def call_lift(self,my_request_id):
        # 让电梯先走的信号 lift_signal
        print(f"请求电梯先行离开{my_request_id}")
        lift_signal = LiftSignal()
        lift_signal.lift_name = self.task_dict[my_request_id]["lift_name"]
        self.lift_signal_publisher.publish(lift_signal)
        # sleep
        time.sleep(30)
        # 用机器人名义呼叫电梯 （直接权限转到机器人身上） 
        robot_name = self.task_dict[my_request_id]["robot_name"]
        print(f"替机器人{robot_name}再次请求电梯{my_request_id}")
        lift_request = LiftRequest()
        lift_request.lift_name = self.task_dict[my_request_id]["lift_name"]
        lift_request.request_time = self.get_clock().now().to_msg()
        lift_request.session_id = str(self.task_dict[my_request_id]["fleet_name"]+"/"+self.task_dict[my_request_id]["robot_name"])
        lift_request.request_type = 1
        lift_request.destination_floor = self.task_dict[my_request_id]["location"]["level_name"]
        lift_request.door_state = 2
        self.lift_request_publisher.publish(lift_request)
        with self._mutex:
            # 说明是被追踪的robot
            self.task_dict[my_request_id]["stage"] = 3 #重新呼叫电梯
        self.call_lift_time = self.get_clock().now().to_msg()



    def confirm_lift(self,msg):
        # 不断获取电梯信息，
        # 确认电梯已到达,并开门，
        
        def lift_ready(lift):
            with self._mutex:
                my_request_id = self.lift_dict[lift]["my_request_id"]
                level_name = self.task_dict[my_request_id]["location"]["level_name"]
                
                # 先判断stage
                if self.task_dict[my_request_id]["stage"]==3 and \
                        msg.door_state == 2 and \
                            msg.session_id == str(self.task_dict[my_request_id]["fleet_name"]+"/"+self.task_dict[my_request_id]["robot_name"]) and \
                                msg.current_floor == level_name :   # 必须是重新呼叫电梯状态时
                    print(msg.lift_time)
                    print(self.call_lift_time)
                    return True
                else :
                    return False

        # 说明是被追踪的lift
        # lift_time = msg.lift_time
        
        lift = msg.lift_name
        if lift in self.lift_dict :
            if lift_ready(lift):
                my_request_id = self.lift_dict[lift]["my_request_id"]
                print(f"电梯{lift}已再次到达{my_request_id}")
                # 恢复任务
                self.send_resume_request(my_request_id)
                del self.lift_dict[lift]     


    def task_response_callback(self, msg):
        # 判断response
        with self._mutex:
            my_request_id = msg.request_id.split('_')[-1]
            my_type =  msg.request_id.split('_')[0]
            if my_request_id in self.task_dict:
                json_msg_=json.loads(msg.json_msg)
                if my_type == "interrupt":
                    print(f"成功中断任务{my_request_id}")
                    if json_msg_["success"] == True:
                        # 保存token
                        token = json_msg_["token"]
                        self.task_dict[my_request_id]["tokens"].append(token)
                        # 发送移动机器人退回目标点请求
                        self.send_robot_location(my_request_id)

                elif my_type == "resume":
                    if json_msg_["success"] == True:
                        print(f"成功恢复任务{my_request_id}")
                        # 判断机器人任务是否恢复
                        # 若恢复从task_dict中删除my_request_id
                        del self.task_dict[my_request_id]



def main(args=None):
    rclpy.init(args=args)
    node = RobotsEnterLift()
    print(f"robots_enter_lift start ...")
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()