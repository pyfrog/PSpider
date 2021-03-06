# _*_ coding: utf-8 _*_

"""
threads_pool.py by xianhu
"""

import copy
import queue
import logging
import threading
from .threads_inst import *
from ..utilities import CONFIG_FETCH_MESSAGE


class ThreadPool(object):
    """
    class of ThreadPool
    """

    def __init__(self, fetcher, parser, saver, proxieser=None, url_filter=None, monitor_sleep_time=5):
        """
        constructor
        """
        self._inst_fetcher = fetcher                    # fetcher instance, subclass of Fetcher
        self._inst_parser = parser                      # parser instance, subclass of Parser
        self._inst_saver = saver                        # saver instance, subclass of Saver
        self._inst_proxieser = proxieser                # default: None, proxieser instance, subclass of Proxieser

        self._queue_fetch = queue.PriorityQueue()       # (priority, counter, url, keys, deep, repeat)
        self._queue_parse = queue.PriorityQueue()       # (priority, counter, url, keys, deep, content)
        self._queue_save = queue.Queue()                # (url, keys, item), item can be anything
        self._queue_proxies = queue.Queue()             # {"http": "http://auth@ip:port", "https": "https://auth@ip:port"}

        self._thread_proxieser = None                   # proxieser thread
        self._thread_fetcher_list = []                  # fetcher threads list
        self._thread_parsar_list = []                   # parser and saver threads list

        self._thread_stop_flag = False                  # default: False, stop flag of threads
        self._url_filter = url_filter                   # default: None, also can be UrlFilter()

        self._number_dict = {
            TPEnum.TASKS_RUNNING: 0,                    # the count of tasks which are running

            TPEnum.URL_FETCH_NOT: 0,                    # the count of urls which haven't been fetched
            TPEnum.URL_FETCH_SUCC: 0,                   # the count of urls which have been fetched successfully
            TPEnum.URL_FETCH_FAIL: 0,                   # the count of urls which have been fetched failed
            TPEnum.URL_FETCH_COUNT: 0,                  # the count of urls which appeared in self._queue_fetch

            TPEnum.HTM_PARSE_NOT: 0,                    # the count of urls which haven't been parsed
            TPEnum.HTM_PARSE_SUCC: 0,                   # the count of urls which have been parsed successfully
            TPEnum.HTM_PARSE_FAIL: 0,                   # the count of urls which have been parsed failed

            TPEnum.ITEM_SAVE_NOT: 0,                    # the count of urls which haven't been saved
            TPEnum.ITEM_SAVE_SUCC: 0,                   # the count of urls which have been saved successfully
            TPEnum.ITEM_SAVE_FAIL: 0,                   # the count of urls which have been saved failed

            TPEnum.PROXIES_LEFT: 0,                     # the count of proxies which are avaliable
            TPEnum.PROXIES_FAIL: 0,                     # the count of proxies which banned by website
        }
        self._lock = threading.Lock()                   # the lock which self._number_dict needs

        # set monitor thread
        self._monitor_flag = True
        self._monitor = MonitorThread("monitor", self, sleep_time=monitor_sleep_time)
        self._monitor.setDaemon(True)
        self._monitor.start()
        return

    def set_start_url(self, url, priority=0, keys=None, deep=0):
        """
        set start url based on "priority", "keys" and "deep", keys must be a dictionary, and repeat must be 0
        """
        self.add_a_task(TPEnum.URL_FETCH, (priority, self.get_number_dict(TPEnum.URL_FETCH_COUNT), url, keys or {}, deep, 0))
        logging.debug("%s set_start_url: %s", self.__class__.__name__, CONFIG_FETCH_MESSAGE % (priority, keys or {}, deep, 0, url))
        return

    def start_working(self, fetcher_num=10):
        """
        start this thread pool
        """
        logging.info("%s start: urls_count=%s, fetcher_num=%s", self.__class__.__name__, self.get_number_dict(TPEnum.URL_FETCH_NOT), fetcher_num)

        self._thread_proxieser = ProxiesThread("proxieser", self._inst_proxieser, self) if self._inst_proxieser else None
        self._thread_fetcher_list = [FetchThread("fetcher-%d" % (i+1), copy.deepcopy(self._inst_fetcher), self) for i in range(fetcher_num)]
        self._thread_parsar_list = [ParseThread("parser", self._inst_parser, self), SaveThread("saver", self._inst_saver, self)]

        if self._thread_proxieser:
            self._thread_proxieser.setDaemon(True)
            self._thread_proxieser.start()

        for thread in self._thread_fetcher_list:
            thread.setDaemon(True)
            thread.start()

        for thread in self._thread_parsar_list:
            thread.setDaemon(True)
            thread.start()

        logging.info("%s start success", self.__class__.__name__)
        return

    def stop_working(self):
        """
        stop this thread pool
        """
        self._thread_stop_flag = True
        logging.info("%s set thread_stop_flag = True", self.__class__.__name__)
        return

    def wait_for_finished(self, is_over=True):
        """
        wait for the finished of this thread pool
        """
        logging.info("%s wait for finished: is_over=%s", self.__class__.__name__, is_over)

        for thread in self._thread_fetcher_list:
            if thread.is_alive():
                thread.join()
        self.clear_queue_fetch()

        for thread in self._thread_parsar_list:
            if thread.is_alive():
                thread.join()

        if self._thread_proxieser and self._thread_proxieser.is_alive():
            self._thread_proxieser.join()

        if is_over and self._monitor.is_alive():
            self._monitor_flag = False
            self._monitor.join()

        logging.info("%s finished: %s", self.__class__.__name__, self._number_dict)
        return self._number_dict

    # ================================================================================================================================
    def get_monitor_flag(self):
        """
        get the monitor flag of this pool
        """
        return self._monitor_flag

    def get_proxies_flag(self):
        """
        get the proxies flag of this pool
        """
        return True if self._inst_proxieser else False

    def get_stop_flag(self):
        """
        get the stop flag of threads
        """
        return self._thread_stop_flag

    def get_current_state(self):
        """
        get current state of this pool
        """
        return self._number_dict

    def get_number_dict(self, key):
        """
        get the value of self._number_dict based on key
        """
        return self._number_dict[key]

    def update_number_dict(self, key, value):
        """
        update the value of self._number_dict based on key
        """
        self._lock.acquire()
        self._number_dict[key] += value
        self._lock.release()
        return

    def is_all_tasks_done(self):
        """
        check if all tasks are done, according to self._number_dict
        """
        return False if self._number_dict[TPEnum.TASKS_RUNNING] or self._number_dict[TPEnum.URL_FETCH_NOT] or \
                        self._number_dict[TPEnum.HTM_PARSE_NOT] or self._number_dict[TPEnum.ITEM_SAVE_NOT] else True

    def clear_queue_fetch(self):
        """
        clear self._queue_fetch
        """
        while self.get_number_dict(TPEnum.URL_FETCH_NOT) > 0:
            priority, _, url, keys, deep, repeat = self.get_a_task(TPEnum.URL_FETCH)
            logging.error("%s error: not fetch, %s", self._inst_fetcher.__class__.__name__, CONFIG_FETCH_MESSAGE % (priority, keys, deep, repeat, url))
            self.update_number_dict(TPEnum.URL_FETCH_FAIL, +1)
            self.finish_a_task(TPEnum.URL_FETCH)
        return

    # ================================================================================================================================
    def add_a_task(self, task_name, task_content):
        """
        add a task based on task_name, also for proxies
        """
        if task_name == TPEnum.PROXIES:
            self._queue_proxies.put_nowait(task_content)
            self.update_number_dict(TPEnum.PROXIES_LEFT, +1)
        elif task_name == TPEnum.URL_FETCH and ((task_content[-1] > 0) or (not self._url_filter) or self._url_filter.check_and_add(task_content[2])):
            self._queue_fetch.put_nowait(task_content)
            self.update_number_dict(TPEnum.URL_FETCH_NOT, +1)
            self.update_number_dict(TPEnum.URL_FETCH_COUNT, +1)
        elif task_name == TPEnum.HTM_PARSE:
            self._queue_parse.put_nowait(task_content)
            self.update_number_dict(TPEnum.HTM_PARSE_NOT, +1)
        elif task_name == TPEnum.ITEM_SAVE:
            self._queue_save.put_nowait(task_content)
            self.update_number_dict(TPEnum.ITEM_SAVE_NOT, +1)
        return

    def get_a_task(self, task_name):
        """
        get a task based on task_name, if queue is empty, raise queue.Empty, also for proxies
        """
        task_content = None
        if task_name == TPEnum.PROXIES:
            task_content = self._queue_proxies.get(block=True, timeout=5)
            self.update_number_dict(TPEnum.PROXIES_LEFT, -1)
            return task_content
        elif task_name == TPEnum.URL_FETCH:
            task_content = self._queue_fetch.get(block=True, timeout=5)
            self.update_number_dict(TPEnum.URL_FETCH_NOT, -1)
        elif task_name == TPEnum.HTM_PARSE:
            task_content = self._queue_parse.get(block=True, timeout=5)
            self.update_number_dict(TPEnum.HTM_PARSE_NOT, -1)
        elif task_name == TPEnum.ITEM_SAVE:
            task_content = self._queue_save.get(block=True, timeout=5)
            self.update_number_dict(TPEnum.ITEM_SAVE_NOT, -1)
        self.update_number_dict(TPEnum.TASKS_RUNNING, +1)
        return task_content

    def finish_a_task(self, task_name):
        """
        finish a task based on task_name, call queue.task_done(), also for proxies
        """
        if task_name == TPEnum.PROXIES:
            self._queue_proxies.task_done()
            return
        elif task_name == TPEnum.URL_FETCH:
            self._queue_fetch.task_done()
        elif task_name == TPEnum.HTM_PARSE:
            self._queue_parse.task_done()
        elif task_name == TPEnum.ITEM_SAVE:
            self._queue_save.task_done()
        self.update_number_dict(TPEnum.TASKS_RUNNING, -1)
        return
    # ================================================================================================================================
