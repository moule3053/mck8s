from kubernetes import client, config
import time
import yaml
import datetime
import kopf
import json

def get_all_federation_clusters():
    config.load_kube_config()

    api_instance = client.CustomObjectsApi()

    group = 'core.kubefed.io'  # str | The custom resource's group name
    version = 'v1beta1'  # str | The custom resource's version
    namespace = 'kube-federation-system'  # str | The custom resource's namespace
    plural = 'kubefedclusters'  # str | The custom resource's plural name. For TPRs this would be lowercase plural kind.
    pretty = 'true'

    api_response = api_instance.list_namespaced_custom_object(group, version, namespace, plural, pretty=pretty)

    clusters = []
    for item in api_response['items']:
        clusters.append(item['metadata']['name'])

    return clusters

def rescheduleApp():

    log_file = "app_rescheduler_logs_3_070221"

    config.load_kube_config()
    api = client.CustomObjectsApi()

    cloud_apps = getCloudApps()

    if len(cloud_apps) > 0:
        for app in cloud_apps:
            app_name = app['name']
            app_spec = app['deployment_spec']

            deleteApp(app_name)
            f = open(log_file, "a")
            f.write("Cloud app " + app_name + " deleted at " + str(datetime.datetime.now()) + "\n")
            f.close()
            print("Cloud app " + app_name + " deleted at " + str(datetime.datetime.now()))

            # Sleep for 60 secs
            # TO DO: Avoid fixed time
            print("Wait for 10 secs before re-creating ................")
            time.sleep(10)

            print("Re-Creating " + app_name + " again ............")
            createApp(app_name, app_spec)

def getCloudApps():
    fog_apps = []

    config.load_kube_config()
    api = client.CustomObjectsApi()

    group = 'fogguru.eu'
    version = 'v1'
    plural = 'multiclusterdeployments'

    api_response = api.list_cluster_custom_object(group=group, version=version, plural=plural, limit=1000)

    for item in api_response['items']:
        dict = {}
        if 'update_fn' in item['status']:
            if any('cloud' in s for s in item['status']['update_fn']['fogapp_locations']):

                dict['deployment_spec'] = item['spec']
                dict['name'] = item['metadata']['name']
                fog_apps.append(dict)

        #elif 'create_fn' in item['status']:
        else:
            if any('cloud' in s for s in item['status']['create_fn']['fogapp_locations']):
                dict['deployment_spec'] = item['spec']
                dict['name'] = item['metadata']['name']
                fog_apps.append(dict)
    return fog_apps

def deleteApp(app_name):
    config.load_kube_config()
    api = client.CustomObjectsApi()

    # Delete fog app on cloud
    print("Deleting app running on the cloud ........", app_name)

    try:
        api.delete_namespaced_custom_object(
            group="fogguru.eu",
            version="v1",
            namespace="default",
            plural="multiclusterdeployments",
            name=app_name,
        )
        print("Application " + app_name + " deleted.")
    except:
        print("Failed to delete " + app_name)

def createApp(app_name, app_spec):
    config.load_kube_config()
    api = client.CustomObjectsApi()

    # Deployment template
    deployment_template = "{'apiVersion': 'fogguru.eu/v1', 'kind': 'MultiClusterDeployment', 'metadata': {'name': '" + app_name + "'}, 'spec': "
    deployment_json = deployment_template + str(app_spec) + "}"
    deployment_text = deployment_json.replace("'", "\"")
    deployment_body = json.loads(deployment_text)

    try:
        api.create_namespaced_custom_object(
            group="fogguru.eu",
            version="v1",
            namespace="default",
            plural="multiclusterdeployments",
            body=deployment_body,
        )
    except:
        # time.sleep(30)
        raise kopf.TemporaryError("Error occurred while patching Multi Cluster Deployment ...............", delay=30)
