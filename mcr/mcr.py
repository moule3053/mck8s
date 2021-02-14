import kopf
import time
from utils import get_all_federation_clusters, rescheduleApp

# Create app rescheduler
@kopf.daemon('fogguru.eu', 'v1', 'appreschedulers', initial_delay=5)
def create_fn(stopped, **kwargs):
    CHECK_PERIOD = 60
    RESCHEDULE_PERIOD = 31 * 60
    while not stopped:
        # for now just rescheduler from cloud to fog
        # TO DO: reschedule pods to users' preferred locations

        # Check if there is a cloud cluster
        all_clusters = get_all_federation_clusters()
        if not any('cloud' in s for s in all_clusters):
            print("There are no cloud clusters. Going to next cycle ....", CHECK_PERIOD)
            time.sleep(CHECK_PERIOD)
        else:
            print("Cloud cluster found. Will start rescheduling after " + str(RESCHEDULE_PERIOD) + " seconds .....")
            time.sleep(RESCHEDULE_PERIOD)
            rescheduleApp()

        print("Sleep for " + str(CHECK_PERIOD) + " secs until next cycle .........")
        time.sleep(CHECK_PERIOD)
