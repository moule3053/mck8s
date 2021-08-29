import kopf
import yaml, pandas as pd
from utils import findPossibleClusters, getFogAppLocations,  getCloudCluster, \
    createDeployment, createService, deleteDeployment, deleteService, patchDeployment, patchService, createJob, \
    deleteJob, patchJob, getMaximumReplicas, findNearestClusters, getAllocatableCapacity, getFogAppClusters, getServiceClusters
import json
import time

# Create multi-cluster deployment
@kopf.on.create('fogguru.eu', 'v1', 'multiclusterdeployments')
def create_fn(body, spec, patch, **kwargs):
    # Get info from multiclusterdeployments object
    fogapp_name = body['metadata']['name']
    fogapp_image = spec['template']['spec']['containers'][0]['image']
    fogapp_replicas = spec['replicas']
    fogapp_cpu_request = int(spec['template']['spec']['containers'][0]['resources']['requests']['cpu'][:-1])
    #fogapp_cpu_limit = spec['template']['spec']['containers']['resources']['limits']['cpu']
    fogapp_memory_request = int(spec['template']['spec']['containers'][0]['resources']['requests']['memory'][:-2])
    #fogapp_memory_limit = spec['template']['spec']['containers']['resources']['limits']['memory']
    #fogapp_type = spec['appType']
    #fogapp_type = body['kind']
    spec_text = str(spec)

    # Make sure image is provided
    if not fogapp_image:
        raise kopf.HandlerFatalError(f"Image must be set. Got {fogapp_image}.")

    if not fogapp_replicas:
        raise kopf.HandlerFatalError(f"Number of replicas must be set. Got {fogapp_replicas}.")

    # Get namespace
    if 'namespace' in body['metadata']:
        fogpapp_namespace = body['metadata']['namespace']
    else:
        fogpapp_namespace = "default"

    # Placement policy specified by user
    if 'placementPolicy' in spec:
        placement_policy = spec['placementPolicy']
    else: # Default placement policy is most_traffic
        placement_policy = 'most_traffic'

    if 'locations' in spec:
        placement_policy = 'cluster_affinity'

    print("The provided placement policy is ..........", placement_policy)

    if 'numberOfLocations' in spec:
        clusters_qty = spec['numberOfLocations']
    else:
        clusters_qty = 1

    eligible_clusters = []

    if 'locations' not in spec:
        mode = 'create'
        fogapp_locations = getFogAppLocations(fogapp_name, fogpapp_namespace, fogapp_cpu_request, fogapp_memory_request, fogapp_replicas, clusters_qty, placement_policy, mode)
        total_replicas = clusters_qty * fogapp_replicas

        if len(fogapp_locations) != 0:
            eligible_clusters = []
            for cluster in fogapp_locations:
                if cluster['max_replicas'] > fogapp_replicas:
                    cluster['replicas'] = fogapp_replicas
                    cluster['overflow'] = 0
                else:
                    cluster['replicas'] = cluster['max_replicas']
                    cluster['overflow'] = fogapp_replicas - cluster['max_replicas']

            total_overflow = 0

            for cluster in fogapp_locations[:clusters_qty]:
                dict = {}
                dict['name'] = cluster['name']
                dict['replicas'] = cluster['replicas']
                eligible_clusters.append(dict)
                total_overflow += cluster['overflow']

            print("Total overflow ...........", total_overflow)

            if total_overflow > 0:
                for cluster in fogapp_locations[clusters_qty:]:
                    if cluster['max_replicas'] > total_overflow:
                        dict = {}
                        dict['name'] = cluster['name']
                        dict['replicas'] = total_overflow
                        total_overflow = 0
                        eligible_clusters.append(dict)
                        break
                    else:
                        dict = {}
                        dict['name'] = cluster['name']
                        dict['replicas'] = cluster['max_replicas']
                        total_overflow = total_overflow - dict['replicas']
                        eligible_clusters.append(dict)

            if total_overflow > 0:
                for cluster in eligible_clusters:
                    if 'cloud' in cluster['name']:
                        cluster['replicas'] += total_overflow
                        total_overflow = 0

            print("Final list of clusters .................", eligible_clusters)
            print("Final overflow .................", total_overflow)

            if total_overflow > 0:
                dict = {}
                dict['message'] = 'to_cloud'
                dict['replicas'] = total_overflow
                patch.status['message'] = dict
                raise kopf.TemporaryError("Fog clusters not sufficient to run the app. Provisioning cloud cluster.....................",
                                          delay=30)
        else:
            dict = {}
            dict['message'] = 'to_cloud'
            dict['replicas'] = fogapp_replicas
            patch.status['message'] = dict
            raise kopf.TemporaryError(
                "No clusters found at the fog level. Provisioning cloud cluster.....................",
                delay=30)
    else:
        input_clusters = spec['locations'].split(",")
        fogapp_locations = []
        for location in input_clusters:
            fogapp_locations.append(location.strip())
        print("Input list of cluster ....", fogapp_locations)
        clusters_qty = len(fogapp_locations)

        if 'replicaOverrides' in spec:
            replicas_list = []
            override_replicas = {}
            if isinstance(spec['replicaOverrides'], str):
                replicas = spec['replicaOverrides'].split(",")
                for i in replicas:
                    replicas_list.append(i.strip())
            elif isinstance(spec['replicaOverrides'], list):
                replicas_list = spec['replicaOverrides']

            print("Replica overrides ............", spec['replicaOverrides'])
            for i in range(0, len(fogapp_locations)):
                override_replicas[fogapp_locations[i]] = replicas_list[i]
        else:
            override_replicas = {}
            for i in range(0, len(fogapp_locations)):
                override_replicas[fogapp_locations[i]] = fogapp_replicas

        total_replicas = 0
        for replica in list(override_replicas.values()):
            total_replicas += int(replica)

        print("Total number of replicas .....", total_replicas)

        fog_only_clusters = []
        for cluster in fogapp_locations:
            if 'cloud' not in cluster:
                fog_only_clusters.append(cluster)

        print("Fog only clusters ..............", fog_only_clusters)

        # Compute cloud replicas
        cloud_replicas = 0
        for cluster in fogapp_locations:
            if 'cloud' in cluster:
                cloud_replicas += int(override_replicas[cluster])

        if len(fog_only_clusters) > 0:
            possible_clusters = findPossibleClusters(fog_only_clusters, fogapp_cpu_request, fogapp_memory_request)
        else:
            possible_clusters = []

        print("Initial possible clusters list ............", possible_clusters)

        # if node of the fog clusters have the right sized nodes
        if len(possible_clusters) == 0:
            eligible_clusters = []
            eligible_replicas = []
            cloud_cluster = getCloudCluster()

            if 'cloud' in cloud_cluster:
                dict = {}
                dict['name'] = cloud_cluster
                dict['replicas'] = total_replicas
                dict['overflow'] = 0
                eligible_clusters.append(dict)
                #eligible_clusters.append(cloud_cluster)
                #eligible_replicas.append(total_replicas)
            else:
                dict = {}
                dict['message'] = 'to_cloud'
                dict['replicas'] = total_replicas
                patch.status['message'] = dict
                raise kopf.TemporaryError("The application could not be scheduled on the Fog elevel. Need cloud cluster.",
                                      delay=30)
            #print("Initial eligible clusters and replicas 1111", eligible_clusters, eligible_replicas)
            print("Initial eligible clusters and replicas 1111", eligible_clusters)
        else:
            fogapp_locations.sort()
            possible_clusters.sort()

            override_replicas_new = {}
            # Check if possible clusters different from input clusters and assign replicas to possible replicas
            # Distribute cloud replicas
            for i in range(0, len(possible_clusters)):
                if possible_clusters[i] in fogapp_locations:
                    override_replicas_new[possible_clusters[i]] = int(override_replicas[possible_clusters[i]]) + int((cloud_replicas / len(possible_clusters)))
                else:
                    override_replicas_new[possible_clusters[i]] = int(list(override_replicas.values())[i]) + int((cloud_replicas / len(possible_clusters)))

            for cluster in possible_clusters:
                replicas = int(override_replicas_new[cluster])
                # is_eligible = checkClusterEligibility(cluster, app_cpu_request, app_memory_request, replicas)
                # The maximum number of replicas the cluster can host
                maximum_replicas = getMaximumReplicas(cluster, fogapp_cpu_request, fogapp_memory_request)
                #maximum_replicas = getAllocatableCapacity(cluster, fogapp_cpu_request, fogapp_memory_request)
                if maximum_replicas > replicas:
                    dict = {}
                    dict['name'] = cluster
                    dict['max_replicas'] = maximum_replicas
                    dict['replicas'] = replicas
                    dict['overflow'] = 0
                    eligible_clusters.append(dict)
                else:
                    dict = {}
                    dict['name'] = cluster
                    dict['max_replicas'] = maximum_replicas
                    dict['replicas'] = maximum_replicas
                    dict['overflow'] = replicas - maximum_replicas
                    eligible_clusters.append(dict)

            temp_list = []
            for cluster in eligible_clusters:
                temp_list.append(cluster)
            print("Possible list of clusters and oveflow ....", temp_list)

            temp_list_2 = []
            for cluster in temp_list:
                temp_list_2.append(cluster['name'])

            temp_list_3 = list(set(fogapp_locations + temp_list_2))

            total_overflow = 0

            for cluster in temp_list:
                total_overflow += cluster['overflow']

            maximum_replicas = {}

            for cluster in temp_list:
                nearest_clusters = []
                overflow = cluster['overflow']
                #leftover = overflow
                print("Overflow from ", cluster, overflow)

                if overflow > 0:
                    nearest_clusters = findNearestClusters(cluster, temp_list_3)
                    print("List of nearest clusters ....", nearest_clusters)

                # Distribute overflow to nearest clusters
                if len(nearest_clusters) > 0:
                    for c in nearest_clusters:
                        # print("Overflow .................", overflow)
                        # if overflow > 0:
                        maximum_replicas[c] = getMaximumReplicas(c, fogapp_cpu_request, fogapp_memory_request)
                        print("Maximum replicas .....", maximum_replicas)

            for cluster in temp_list:
                nearest_clusters = []
                overflow = cluster['overflow']
                if overflow > 0:
                    nearest_clusters = findNearestClusters(cluster, temp_list_3)
                # else:
                #     break
                if len(nearest_clusters) > 0:
                    for c in nearest_clusters:
                        if cluster['overflow'] > 0:
                            if maximum_replicas[c] == 0:
                                cluster['overflow'] = cluster['overflow']
                                #break
                            elif maximum_replicas[c] > cluster['overflow']:
                                dict = {}
                                dict['name'] = c
                                dict['replicas'] = cluster['overflow']
                                dict['overflow'] = 0
                                eligible_clusters.append(dict)
                                maximum_replicas[c] = maximum_replicas[c] - cluster['overflow']
                                cluster['overflow'] = 0
                                #break
                            else:
                                dict = {}
                                dict['name'] = c
                                dict['replicas'] = maximum_replicas[c]
                                dict['overflow'] = 0
                                cluster['overflow'] = cluster['overflow'] - maximum_replicas[c]
                                eligible_clusters.append(dict)
                                maximum_replicas[c] = 0

            # Group clusters and replicas
            eligible_clusters = (pd.DataFrame(eligible_clusters)
                                 .groupby(['name'], as_index=False)
                                 .agg({'replicas': 'sum', 'overflow': 'sum'})
                                 .to_dict('r'))

            print("Preliminary list of eligible clusters ...", eligible_clusters)

            # Compute leftover to be deployed on cloud cluster
            leftover = 0

            for cluster in eligible_clusters:
                if cluster['overflow'] > 0:
                    leftover += cluster['overflow']

            if leftover > 0:
                for cluster in fogapp_locations:
                    if 'cloud' in cluster:
                        dict = {}
                        dict['name'] = cluster
                        dict['replicas'] = leftover
                        dict['overflow'] = 0
                        eligible_clusters.append(dict)
                        leftover = 0
                        print("Eligible clusters including cloud ...........", eligible_clusters)

            if len(eligible_clusters) == 0:
                dict = {}
                dict['message'] = 'to_cloud'
                dict['replicas'] = total_replicas
                patch.status['message'] = dict
                raise kopf.TemporaryError(
                    "The application could not be scheduled on the Fog elevel. Need cloud cluster.",
                    delay=30)
            else:
                if leftover > 0:
                    cloud_cluster = getCloudCluster()
                    if 'cloud' in cloud_cluster:
                        dict = {}
                        dict['name'] = cloud_cluster
                        dict['replicas'] = leftover
                        dict['overflow'] = 0
                        eligible_clusters.append(dict)
                        leftover = 0
                        print("Eligible clusters including cloud ...........", eligible_clusters)
                    else:
                        dict = {}
                        dict['message'] = 'to_cloud'
                        dict['replicas'] = leftover
                        patch.status['message'] = dict
                        raise kopf.TemporaryError(
                            "The application could not be scheduled on the Fog level. Need cloud cluster.",
                            delay=30)

    for cluster in eligible_clusters:
        if cluster['replicas'] == 0:
            eligible_clusters.remove(cluster)

    print("Final list of eligible clusters ...", eligible_clusters)

    temp_list = []
    for cluster in eligible_clusters:
        temp_list.append(cluster)

    eligible_clusters = []
    eligible_replicas = []

    print("Deploy temp list ,,,,,,,,,,,,,,,,,,", temp_list)

    for cluster in temp_list:
        eligible_clusters.append(cluster['name'])
        eligible_replicas.append(cluster['replicas'])

    # For the spec file
    deployment_template = "{'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': '" + fogapp_name + "', 'namespace': '" + fogpapp_namespace +"'}, 'spec': "
    deployment_json = deployment_template + spec_text + "}"
    deployment_text = deployment_json.replace("'", "\"")
    deployment_body = json.loads(deployment_text)


    i = 0

    for cluster in eligible_clusters:
        # Update replicas per cluster
        deployment_body['spec']['replicas'] = eligible_replicas[i]

        createDeployment(cluster, deployment_body, fogpapp_namespace)

        i += 1

    dict = {}
    dict['message'] = 'provisioned'
    dict['replicas'] = eligible_replicas
    patch.status['message'] = dict

    # TO DO: per cluster overrides
    return {'fogapp_name': fogapp_name, 'fogapp_namespace': fogpapp_namespace, 'input_clusters': fogapp_locations, 'input_replicas': fogapp_replicas, 'fogapp_replicas': eligible_replicas, 'fogapp_locations': eligible_clusters, 'fogapp_status': 'provisioned'}

# Update or patch initial placement (e.g., change number of replicas, Docker image for the app, locations, etc.)
@kopf.on.update('fogguru.eu', 'v1', 'multiclusterdeployments')
def update_fn(spec, status, body, namespace, logger, patch, **kwargs):

    # TO DO: the case of an multiclusterdeployment which failed initially
    # Update doesn't work since the child objects could not be found
    # In such a case may be go to create ?

    fogapp_name = status['create_fn']['fogapp_name']
    fogapp_image = spec['template']['spec']['containers'][0]['image']
    fogapp_replicas = spec['replicas']
    fogapp_cpu_request = int(spec['template']['spec']['containers'][0]['resources']['requests']['cpu'][:-1])
    # fogapp_cpu_limit = spec['template']['spec']['containers']['resources']['limits']['cpu']
    fogapp_memory_request = int(spec['template']['spec']['containers'][0]['resources']['requests']['memory'][:-2])
    # fogapp_memory_limit = spec['template']['spec']['containers']['resources']['limits']['memory']
    # fogapp_type = spec['appType']
    # fogapp_type = body['kind']
    spec_text = str(spec)

    fogapp_current_replicas = {}

    if 'update_fn' in status:
        fogapp_current_locations = status['update_fn']['fogapp_locations']
        for i in range(0, len(fogapp_current_locations)):
            fogapp_current_replicas[fogapp_current_locations[i]] = status['update_fn']['fogapp_replicas'][i]
    else:
        fogapp_current_locations = status['create_fn']['fogapp_locations']
        for i in range(0, len(fogapp_current_locations)):
            fogapp_current_replicas[fogapp_current_locations[i]] = status['create_fn']['fogapp_replicas'][i]

    total_current_replicas = 0

    for cluster in fogapp_current_locations:
        total_current_replicas += fogapp_current_replicas[cluster]

    print("Current locations and replicas ............................", fogapp_current_replicas)

    # if not fogapp_type or 'appType' not in spec:
    #     raise kopf.HandlerFatalError(f"appType needs to be specified.")

    # Make sure image is provided
    if not fogapp_image:
        raise kopf.HandlerFatalError(f"Image must be set. Got {fogapp_image}.")

    if not fogapp_replicas:
        raise kopf.HandlerFatalError(f"Number of replicas must be set. Got {fogapp_replicas}.")

    if 'numberOfLocations' in spec:
        clusters_qty = spec['numberOfLocations']
    else:
        clusters_qty = 1

    # Get namespace
    if 'namespace' in body['metadata']:
        fogpapp_namespace = body['metadata']['namespace']
    else:
        fogpapp_namespace = "default"

    # Placement policy specified by user
    if 'placementPolicy' in spec:
        placement_policy = spec['placementPolicy']
    else:  # Default placement policy is most_traffic
        placement_policy = 'most_traffic'

    override_replicas = {}
    eligible_replicas = []

    eligible_clusters = []

    if 'locations' not in spec:
        mode = 'update'
        fogapp_locations = getFogAppLocations(fogapp_name, fogpapp_namespace, fogapp_cpu_request, fogapp_memory_request, fogapp_replicas, clusters_qty, placement_policy, mode)
        total_replicas = clusters_qty * fogapp_replicas

        if len(fogapp_locations) != 0:
            eligible_clusters = []
            for cluster in fogapp_locations:
                if cluster['max_replicas'] > fogapp_replicas:
                    cluster['replicas'] = fogapp_replicas
                    cluster['overflow'] = 0
                else:
                    cluster['replicas'] = cluster['max_replicas']
                    cluster['overflow'] = fogapp_replicas - cluster['max_replicas']

            total_overflow = 0

            for cluster in fogapp_locations[:clusters_qty]:
                dict = {}
                dict['name'] = cluster['name']
                dict['replicas'] = cluster['replicas']
                eligible_clusters.append(dict)
                total_overflow += cluster['overflow']

            print("Total overflow ...........", total_overflow)

            if total_overflow > 0:
                for cluster in fogapp_locations[clusters_qty:]:
                    if cluster['max_replicas'] > total_overflow:
                        dict = {}
                        dict['name'] = cluster['name']
                        dict['replicas'] = total_overflow
                        total_overflow = 0
                        eligible_clusters.append(dict)
                        break
                    else:
                        dict = {}
                        dict['name'] = cluster['name']
                        dict['replicas'] = cluster['max_replicas']
                        total_overflow = total_overflow - dict['replicas']
                        eligible_clusters.append(dict)

            if total_overflow > 0:
                for cluster in eligible_clusters:
                    if 'cloud' in cluster['name']:
                        cluster['replicas'] += total_overflow
                        total_overflow = 0

            print("Final list of clusters .................", eligible_clusters)
            print("Final overflow .................", total_overflow)

            if total_overflow > 0:
                dict = {}
                dict['message'] = 'to_cloud'
                dict['replicas'] = total_overflow
                patch.status['message'] = dict
                raise kopf.TemporaryError("Fog clusters not sufficient to run the app. Provisioning cloud cluster.....................",
                                          delay=30)
        else:
            dict = {}
            dict['message'] = 'to_cloud'
            dict['replicas'] = fogapp_replicas
            patch.status['message'] = dict
            raise kopf.TemporaryError(
                "No clusters found at the fog level. Provisioning cloud cluster.....................",
                delay=30)
    else:
        input_clusters = spec['locations'].split(",")
        fogapp_locations = []
        for location in input_clusters:
            fogapp_locations.append(location.strip())
        print("Input list of clusters ....", fogapp_locations)
        clusters_qty = len(fogapp_locations)

        if 'replicaOverrides' in spec:
            replicas_list = []
            override_replicas = {}
            if isinstance(spec['replicaOverrides'], str):
                replicas = spec['replicaOverrides'].split(",")
                for i in replicas:
                    replicas_list.append(i.strip())
            elif isinstance(spec['replicaOverrides'], list):
                replicas_list = spec['replicaOverrides']

            print("Replica overrides ............", spec['replicaOverrides'])
            for i in range(0, len(fogapp_locations)):
                override_replicas[fogapp_locations[i]] = replicas_list[i]
        else:
            override_replicas = {}
            for i in range(0, len(fogapp_locations)):
                override_replicas[fogapp_locations[i]] = fogapp_replicas

        print("Replica overrides input ....", override_replicas)

        total_replicas = 0
        for replica in list(override_replicas.values()):
            total_replicas += int(replica)

        print("Total number of replicas .....", total_replicas)

        fog_only_clusters = []
        for cluster in fogapp_locations:
            if 'cloud' not in cluster:
                fog_only_clusters.append(cluster)

        # Compute cloud replicas
        cloud_replicas = 0
        for cluster in fogapp_locations:
            if 'cloud' in cluster:
                cloud_replicas += int(override_replicas[cluster])

        if len(fog_only_clusters) > 0:
            possible_clusters = findPossibleClusters(fog_only_clusters, fogapp_cpu_request, fogapp_memory_request)
        else:
            possible_clusters = []

        print("Initial possible clusters list ............", possible_clusters)

        # if node of the fog clusters have the right sized nodes
        if len(possible_clusters) == 0:
            eligible_clusters = []
            eligible_replicas = []
            cloud_cluster = getCloudCluster()

            if 'cloud' in cloud_cluster:
                # eligible_clusters.append(cloud_cluster)
                # eligible_replicas.append(total_replicas)
                dict = {}
                dict['name'] = cloud_cluster
                dict['replicas'] = total_replicas
                eligible_clusters.append(dict)
            else:
                dict = {}
                dict['message'] = 'to_cloud'
                dict['replicas'] = total_replicas
                patch.status['message'] = dict
                raise kopf.TemporaryError("The application could not be scheduled on the Fog elevel. Need cloud cluster.",
                                      delay=30)
            print("Initial eligible clusters and replicas 1111", eligible_clusters)
        else:
            fogapp_locations.sort()
            possible_clusters.sort()

            override_replicas_update = {}

            # Assign replicas to replacement clusters from input clusters
            for i in range(0, len(possible_clusters)):
                if possible_clusters[i] in fogapp_locations:
                    override_replicas_update[possible_clusters[i]] = override_replicas[possible_clusters[i]]
                else:
                    override_replicas_update[possible_clusters[i]] = list(override_replicas.values())[i]

            print("Override replicas new .....", override_replicas_update)

            for cluster in possible_clusters:
                replicas = int(override_replicas_update[cluster])
                #replicas = int(override_replicas_diff[cluster])
                # is_eligible = checkClusterEligibility(cluster, app_cpu_request, app_memory_request, replicas)
                # The maximum number of replicas the cluster can host
                maximum_replicas = getAllocatableCapacity(cluster, fogapp_cpu_request, fogapp_memory_request, fogapp_name, fogpapp_namespace)
                if maximum_replicas > replicas:
                    dict = {}
                    dict['name'] = cluster
                    dict['max_replicas'] = maximum_replicas
                    dict['replicas'] = replicas
                    dict['overflow'] = 0
                    eligible_clusters.append(dict)
                else:
                    dict = {}
                    dict['name'] = cluster
                    dict['max_replicas'] = maximum_replicas
                    dict['replicas'] = maximum_replicas
                    dict['overflow'] = replicas - maximum_replicas
                    eligible_clusters.append(dict)

            temp_list = []
            for cluster in eligible_clusters:
                temp_list.append(cluster)
            print("Possible list of clusters and oveflow ....", temp_list)

            temp_list_2 = []
            for cluster in temp_list:
                temp_list_2.append(cluster['name'])

            temp_list_3 = list(set(fogapp_locations + temp_list_2))

            total_overflow = 0

            for cluster in temp_list:
                total_overflow += cluster['overflow']

            maximum_replicas = {}

            for cluster in temp_list:
                nearest_clusters = []
                overflow = cluster['overflow']
                #leftover = overflow
                print("Overflow from ", cluster, overflow)

                if overflow > 0:
                    nearest_clusters = findNearestClusters(cluster, temp_list_3)
                    print("List of nearest clusters ....", nearest_clusters)
                # else:
                #     print("The cluster doesn't have overflow ....")
                #     break

                # Distribute overflow to nearest clusters
                if len(nearest_clusters) > 0:
                    for c in nearest_clusters:
                        # print("Overflow .................", overflow)
                        # if overflow > 0:
                        maximum_replicas[c] = getAllocatableCapacity(c, fogapp_cpu_request, fogapp_memory_request, fogapp_name, fogpapp_namespace)
                        print("Maximum replicas .....", maximum_replicas)

            for cluster in temp_list:
                nearest_clusters = []
                overflow = cluster['overflow']
                if overflow > 0:
                    nearest_clusters = findNearestClusters(cluster, temp_list_3)
                # else:
                #     break
                if len(nearest_clusters) > 0:
                    for c in nearest_clusters:
                        if cluster['overflow'] > 0:
                            if maximum_replicas[c] == 0:
                                cluster['overflow'] = cluster['overflow']
                                #break
                            elif maximum_replicas[c] > cluster['overflow']:
                                dict = {}
                                dict['name'] = c
                                dict['replicas'] = cluster['overflow']
                                dict['overflow'] = 0
                                eligible_clusters.append(dict)
                                maximum_replicas[c] = maximum_replicas[c] - cluster['overflow']
                                cluster['overflow'] = 0
                                #break
                            else:
                                dict = {}
                                dict['name'] = c
                                dict['replicas'] = maximum_replicas[c]
                                dict['overflow'] = 0
                                cluster['overflow'] = cluster['overflow'] - maximum_replicas[c]
                                eligible_clusters.append(dict)
                                maximum_replicas[c] = 0


            eligible_clusters = (pd.DataFrame(eligible_clusters)
                                 .groupby(['name'], as_index=False)
                                 .agg({'replicas': 'sum', 'overflow': 'sum'})
                                 .to_dict('r'))

            # for c in eligible_clusters:
            #     maximum_replicas = getMaximumReplicas(c['name'], fogapp_cpu_request, fogapp_memory_request)
            #     if c['replicas'] > maximum_replicas:
            #         c['overflow'] = c['overflow'] + c['replicas'] - maximum_replicas
            #         c['replicas'] = maximum_replicas

            print("Preliminary list of eligible clusters ...", eligible_clusters)

            # Compute leftover to be deployed on cloud cluster
            leftover = 0

            for cluster in eligible_clusters:
                if cluster['overflow'] > 0:
                    leftover += cluster['overflow']

            # Add leftover on top of the number of replicas requested for cloud
            # for cluster in fogapp_locations:
            #     if 'cloud' in cluster:
            #         leftover += int(override_replicas[cluster])

            if leftover > 0:
                for cluster in fogapp_locations:
                    if 'cloud' in cluster:
                        dict = {}
                        dict['name'] = cluster
                        dict['replicas'] = leftover
                        dict['overflow'] = 0
                        eligible_clusters.append(dict)
                        leftover = 0
                        print("Eligible clusters including cloud ...........", eligible_clusters)

            if len(eligible_clusters) == 0:
                dict = {}
                dict['message'] = 'to_cloud'
                dict['replicas'] = total_replicas
                patch.status['message'] = dict
                raise kopf.TemporaryError(
                    "The application could not be scheduled on the Fog level. Need cloud cluster.",
                    delay=30)
            else:
                if leftover > 0:
                    cloud_cluster = getCloudCluster()
                    if 'cloud' in cloud_cluster:
                        dict = {}
                        dict['name'] = cloud_cluster
                        dict['replicas'] = leftover
                        dict['overflow'] = 0
                        eligible_clusters.append(dict)
                        leftover = 0
                        print("Eligible clusters including cloud ...........", eligible_clusters)
                    else:
                        dict = {}
                        dict['message'] = 'to_cloud'
                        dict['replicas'] = leftover
                        patch.status['message'] = dict
                        raise kopf.TemporaryError(
                            "The application could not be scheduled on the Fog level. Need cloud cluster.",
                            delay=30)
    for cluster in eligible_clusters:
        if cluster['replicas'] == 0:
            eligible_clusters.remove(cluster)

    print("Final list of eligible clusters ...", eligible_clusters)

    temp_list = []
    for cluster in eligible_clusters:
        temp_list.append(cluster)

    eligible_clusters = []
    eligible_replicas = []

    for cluster in temp_list:
        eligible_clusters.append(cluster['name'])
        eligible_replicas.append(cluster['replicas'])

    # For the spec file
    deployment_template = "{'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': '" + fogapp_name + "', 'namespace': '" + fogpapp_namespace + "'}, 'spec': "
    deployment_json = deployment_template + spec_text + "}"
    deployment_text = deployment_json.replace("'", "\"")
    deployment_body = json.loads(deployment_text)

    # Delete deployment and service from current clusters
    fogapp_current_locations.sort()

    eligible_clusters_sorted = []
    for cluster in eligible_clusters:
        eligible_clusters_sorted.append(cluster)
    eligible_clusters_sorted.sort()

    if len(eligible_clusters_sorted) == len(fogapp_current_locations) and fogapp_current_locations == eligible_clusters_sorted:
        print("Same set of clusters .... Patching ......")
        i = 0
        for cluster in eligible_clusters:
            deployment_body['spec']['replicas'] = eligible_replicas[i]
            print("Patching fogapp on existing clusters ............")
            patchDeployment(cluster, fogapp_name, deployment_body, fogpapp_namespace)
            i += 1
    elif len(eligible_clusters_sorted) == len(fogapp_current_locations) and fogapp_current_locations != eligible_clusters_sorted:
        i = 0
        for cluster in fogapp_current_locations:
            if cluster not in eligible_clusters:
                print("Delete app from current clusters")
                deleteDeployment(cluster, fogapp_name, fogpapp_namespace)
        for cluster in eligible_clusters:
            if cluster not in fogapp_current_locations:
                deployment_body['spec']['replicas'] = eligible_replicas[i]
                print("Creating fogapp on more clusters ...........")
                createDeployment(cluster, deployment_body, fogpapp_namespace)
            i += 1

    if len(eligible_clusters_sorted) > len(fogapp_current_locations):
        i = 0
        for cluster in eligible_clusters:
            if cluster in fogapp_current_locations:
                deployment_body['spec']['replicas'] = eligible_replicas[i]
                print("Patching fogapp on existing clusters ............")
                patchDeployment(cluster, fogapp_name, deployment_body, fogpapp_namespace)
            else:
                deployment_body['spec']['replicas'] = eligible_replicas[i]
                print("Creating fogapp on more clusters ...........")
                createDeployment(cluster, deployment_body, fogpapp_namespace)
            i += 1

    if len(eligible_clusters_sorted) < len(fogapp_current_locations):
        i = 0
        for cluster in fogapp_current_locations:
            if cluster in eligible_clusters:
                deployment_body['spec']['replicas'] = eligible_replicas[i]
                print("Patching fogapp on existing clusters ............")
                patchDeployment(cluster, fogapp_name, deployment_body, fogpapp_namespace)
                i += 1
            else:
                print("Delete app from current clusters")
                deleteDeployment(cluster, fogapp_name, fogpapp_namespace)
                #i += 1

    dict = {}
    dict['message'] = 'provisioned'
    dict['replicas'] = eligible_replicas
    patch.status['message'] = dict

    return {'fogapp_name': fogapp_name, 'fogapp_namespace': fogpapp_namespace, 'fogapp_replicas': eligible_replicas,
            'fogapp_locations': eligible_clusters, 'fogapp_status': 'provisioned'}

# Delete multiclusterdeployments, thus delete the associated deployments
@kopf.on.delete('fogguru.eu', 'v1', 'multiclusterdeployments')
def delete(spec, body, status, **kwargs):
    fogapp_name = body['metadata']['name']
    #fogapp_type = spec['appType']

    fogapp_locations_update = []
    fogapp_locations_create = []

    # Get namespace
    if 'namespace' in body['metadata']:
        fogpapp_namespace = body['metadata']['namespace']
    else:
        fogpapp_namespace = "default"

    if 'update_fn' in status:
        fogapp_locations_update = status['update_fn']['fogapp_locations']
    if 'create_fn' in status:
        fogapp_locations_create = status['create_fn']['fogapp_locations']

    for cluster in fogapp_locations_update:
        try:
            deleteDeployment(cluster, fogapp_name, fogpapp_namespace)
        except:
            pass

    for cluster in fogapp_locations_create:
        try:
            deleteDeployment(cluster, fogapp_name, fogpapp_namespace)
        except:
            pass

    msg = f"Multi Cluster Deployment {fogapp_name} and its deployments are DELETED!"
    return {'message': msg}

# Create multi-cluster service
@kopf.on.create('fogguru.eu', 'v1', 'multiclusterservices')
def create_fn(body, spec, meta, patch, **kwargs):
    fogapp_name = body['metadata']['name']

    # Get namespace
    if 'namespace' in body['metadata']:
        fogpapp_namespace = body['metadata']['namespace']
    else:
        fogpapp_namespace = "default"

    spec_text = str(spec)

    # For the spec file
    if 'io.cilium/global-service' in meta['annotations']:
        service_template = "{'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'annotations':{'io.cilium/global-service':'true'}, 'name': '" + fogapp_name + "', 'namespace': '" + fogpapp_namespace + "'}, 'spec': "
    elif 'external-dns.alpha.kubernetes.io/internal-hostname' in meta['annotations']:
        service_template = "{'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'annotations':{'external-dns.alpha.kubernetes.io/hostname':'" + fogapp_name + ".fogguru.apps'}, 'name': '" + fogapp_name + "', 'namespace': '" + fogpapp_namespace + "'}, 'spec': "
    else:
        service_template = "{'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': '" + fogapp_name + "', 'namespace': '" + fogpapp_namespace + "'}, 'spec': "

    service_json = service_template + spec_text + "}"
    service_text = service_json.replace("'", "\"")
    service_body = json.loads(service_text)

    if 'locations' not in spec:
        current_clusters, original_clusters = getFogAppClusters(fogapp_name, fogpapp_namespace)
    else:
        input_clusters = spec['locations'].split(",")
        current_clusters = []
        for location in input_clusters:
            current_clusters.append(location.strip())

    for cluster in current_clusters:
        createService(cluster, service_body, fogpapp_namespace)

    return {'fogapp_name': fogapp_name, 'fogapp_namespace': fogpapp_namespace, 'fogapp_locations': current_clusters, 'fogapp_status': 'provisioned'}

# Update multi-cluster service
@kopf.on.update('fogguru.eu', 'v1', 'multiclusterservices')
def update_fn(body, spec, meta, patch, **kwargs):
    fogapp_name = body['metadata']['name']
    spec_text = str(spec)

    # For the spec file
    if 'io.cilium/global-service' in meta['annotations']:
        service_template = "{'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'annotations':{'io.cilium/global-service':'true'}, 'name': '" + fogapp_name + "'}, 'spec': "
    elif 'external-dns.alpha.kubernetes.io/internal-hostname' in meta['annotations']:
        service_template = "{'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'annotations':{'external-dns.alpha.kubernetes.io/hostname':'" + fogapp_name + ".fogguru.apps'}, 'name': '" + fogapp_name + "'}, 'spec': "
    else:
        service_template = "{'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': '" + fogapp_name + "'}, 'spec': "
    service_json = service_template + spec_text + "}"
    service_text = service_json.replace("'", "\"")
    service_body = json.loads(service_text)

    # Get namespace
    if 'namespace' in body['metadata']:
        fogpapp_namespace = body['metadata']['namespace']
    else:
        fogpapp_namespace = "default"

    if 'locations' not in spec:
        current_deployment_clusters, original_deployment_clusters = getFogAppClusters(fogapp_name, fogpapp_namespace)
    else:
        input_clusters = spec['locations'].split(",")
        current_deployment_clusters = []
        for location in input_clusters:
            current_deployment_clusters.append(location.strip())

    current_service_clusters, original_service_clusters = getServiceClusters(fogapp_name, fogpapp_namespace)

    current_deployment_clusters.sort()
    current_service_clusters.sort()

    if current_service_clusters == current_deployment_clusters:
        pass
    elif len(current_service_clusters) == len(current_deployment_clusters) and current_service_clusters != current_deployment_clusters:
        for cluster in current_service_clusters:
            if cluster not in current_deployment_clusters:
                print("Delete service from current clusters ...............")
                deleteService(cluster, fogapp_name, fogpapp_namespace)

        for cluster in current_deployment_clusters:
            if cluster not in current_service_clusters:
                print("Create service in new clusters ...........")
                createService(cluster, service_body)

    if len(current_service_clusters) < len(current_deployment_clusters):
        for cluster in current_deployment_clusters:
            if cluster in current_service_clusters:
                pass
            else:
                print("Create service in new clusters ...........")
                createService(cluster, service_body)
    elif len(current_service_clusters) > len(current_deployment_clusters):
        for cluster in current_service_clusters:
            if cluster in current_deployment_clusters:
                pass
            else:
                print("Delete service from current clusters ...............")
                deleteService(cluster, fogapp_name, fogpapp_namespace)

    return {'fogapp_name': fogapp_name, 'fogapp_namespace': fogpapp_namespace, 'fogapp_locations': current_deployment_clusters, 'fogapp_status': 'provisioned'}

# Delete multi-cluster service
@kopf.on.delete('fogguru.eu', 'v1', 'multiclusterservices')
def delete_fn(body, spec, patch, **kwargs):
    fogapp_name = body['metadata']['name']

    # Get namespace
    if 'namespace' in body['metadata']:
        fogpapp_namespace = body['metadata']['namespace']
    else:
        fogpapp_namespace = "default"

    current_clusters, original_clusters = getServiceClusters(fogapp_name, fogpapp_namespace)

    for cluster in current_clusters:
        deleteService(cluster, fogapp_name, fogpapp_namespace)

    msg = f"Multi Cluster Service {fogapp_name} is DELETED!"
    return {'message': msg}

# Multi Cluster Job
@kopf.on.create('fogguru.eu', 'v1', 'multiclusterjobs')
def create_fn(body, spec, patch, **kwargs):
    # Get info from multiclusterjobs object
    fogapp_name = body['metadata']['name']
    fogapp_image = spec['template']['spec']['containers'][0]['image']
    #fogapp_replicas = spec['replicas']
    fogapp_replicas = 1
    fogapp_cpu_request = int(spec['template']['spec']['containers'][0]['resources']['requests']['cpu'][:-1])
    #fogapp_cpu_limit = spec['template']['spec']['containers']['resources']['limits']['cpu']
    fogapp_memory_request = int(spec['template']['spec']['containers'][0]['resources']['requests']['memory'][:-2])
    #fogapp_memory_limit = spec['template']['spec']['containers']['resources']['limits']['memory']
    #fogapp_type = spec['appType']
    #fogapp_type = body['kind']
    spec_text = str(spec)

    # Make sure image is provided
    if not fogapp_image:
        raise kopf.HandlerFatalError(f"Image must be set. Got {fogapp_image}.")

    if not fogapp_replicas:
        raise kopf.HandlerFatalError(f"Number of replicas must be set. Got {fogapp_replicas}.")

    # Get namespace
    if 'namespace' in body['metadata']:
        fogpapp_namespace = body['metadata']['namespace']
    else:
        fogpapp_namespace = "default"

    # Placement policy specified by user
    if 'placementPolicy' in spec:
        placement_policy = spec['placementPolicy']
    else:  # Default placement policy is most_traffic
        placement_policy = 'most_traffic'

    if 'locations' in spec:
        placement_policy = 'cluster_affinity'

    print("The provided placement policy is ..........", placement_policy)

    if 'numberOfLocations' in spec:
        clusters_qty = spec['numberOfLocations']
    else:
        clusters_qty = 1

    eligible_clusters = []

    if 'locations' not in spec:
        mode = 'create'
        fogapp_locations = getFogAppLocations(fogapp_name, fogpapp_namespace, fogapp_cpu_request, fogapp_memory_request, fogapp_replicas, clusters_qty, placement_policy, mode)
        total_replicas = clusters_qty * fogapp_replicas

        if len(fogapp_locations) != 0:
            eligible_clusters = []
            for cluster in fogapp_locations:
                if cluster['max_replicas'] > fogapp_replicas:
                    cluster['replicas'] = fogapp_replicas
                    cluster['overflow'] = 0
                else:
                    cluster['replicas'] = cluster['max_replicas']
                    cluster['overflow'] = fogapp_replicas - cluster['max_replicas']

            total_overflow = 0

            for cluster in fogapp_locations[:clusters_qty]:
                dict = {}
                dict['name'] = cluster['name']
                dict['replicas'] = cluster['replicas']
                eligible_clusters.append(dict)
                total_overflow += cluster['overflow']

            print("Total overflow ...........", total_overflow)

            if total_overflow > 0:
                for cluster in fogapp_locations[clusters_qty:]:
                    if cluster['max_replicas'] > total_overflow:
                        dict = {}
                        dict['name'] = cluster['name']
                        dict['replicas'] = total_overflow
                        total_overflow = 0
                        eligible_clusters.append(dict)
                        break
                    else:
                        dict = {}
                        dict['name'] = cluster['name']
                        dict['replicas'] = cluster['max_replicas']
                        total_overflow = total_overflow - dict['replicas']
                        eligible_clusters.append(dict)

            if total_overflow > 0:
                for cluster in eligible_clusters:
                    if 'cloud' in cluster['name']:
                        cluster['replicas'] += total_overflow
                        total_overflow = 0

            print("Final list of clusters .................", eligible_clusters)
            print("Final overflow .................", total_overflow)

            if total_overflow > 0:
                dict = {}
                dict['message'] = 'to_cloud'
                dict['replicas'] = total_overflow
                patch.status['message'] = dict
                raise kopf.TemporaryError("Fog clusters not sufficient to run the app. Provisioning cloud cluster.....................",
                                          delay=30)
        else:
            dict = {}
            dict['message'] = 'to_cloud'
            dict['replicas'] = fogapp_replicas
            patch.status['message'] = dict
            raise kopf.TemporaryError(
                "No clusters found at the fog level. Provisioning cloud cluster.....................",
                delay=30)
    else:
        input_clusters = spec['locations'].split(",")
        fogapp_locations = []
        for location in input_clusters:
            fogapp_locations.append(location.strip())
        print("Input list of cluster ....", fogapp_locations)
        clusters_qty = len(fogapp_locations)

        if 'replicaOverrides' in spec:
            replicas_list = []
            override_replicas = {}
            if isinstance(spec['replicaOverrides'], str):
                replicas = spec['replicaOverrides'].split(",")
                for i in replicas:
                    replicas_list.append(i.strip())
            elif isinstance(spec['replicaOverrides'], list):
                replicas_list = spec['replicaOverrides']

            print("Replica overrides ............", spec['replicaOverrides'])
            for i in range(0, len(fogapp_locations)):
                override_replicas[fogapp_locations[i]] = replicas_list[i]
        else:
            override_replicas = {}
            for i in range(0, len(fogapp_locations)):
                override_replicas[fogapp_locations[i]] = fogapp_replicas

        total_replicas = 0
        for replica in list(override_replicas.values()):
            total_replicas += int(replica)

        print("Total number of replicas .....", total_replicas)

        fog_only_clusters = []
        for cluster in fogapp_locations:
            if 'cloud' not in cluster:
                fog_only_clusters.append(cluster)

        print("Fog only clusters ..............", fog_only_clusters)

        # Compute cloud replicas
        cloud_replicas = 0
        for cluster in fogapp_locations:
            if 'cloud' in cluster:
                cloud_replicas += int(override_replicas[cluster])

        if len(fog_only_clusters) > 0:
            possible_clusters = findPossibleClusters(fog_only_clusters, fogapp_cpu_request, fogapp_memory_request)
        else:
            possible_clusters = []

        print("Initial possible clusters list ............", possible_clusters)

        # if node of the fog clusters have the right sized nodes
        if len(possible_clusters) == 0:
            eligible_clusters = []
            eligible_replicas = []
            cloud_cluster = getCloudCluster()

            if 'cloud' in cloud_cluster:
                dict = {}
                dict['name'] = cloud_cluster
                dict['replicas'] = total_replicas
                eligible_clusters.append(dict)
            else:
                dict = {}
                dict['message'] = 'to_cloud'
                dict['replicas'] = total_replicas
                patch.status['message'] = dict
                raise kopf.TemporaryError("The application could not be scheduled on the Fog elevel. Need cloud cluster.",
                                      delay=30)
            print("Initial eligible clusters and replicas 1111", eligible_clusters)
        else:
            fogapp_locations.sort()
            possible_clusters.sort()

            override_replicas_new = {}
            # Check if possible clusters different from input clusters and assign replicas to possible replicas
            # Distribute cloud replicas
            for i in range(0, len(possible_clusters)):
                if possible_clusters[i] in fogapp_locations:
                    override_replicas_new[possible_clusters[i]] = int(override_replicas[possible_clusters[i]]) + int((cloud_replicas / len(possible_clusters)))
                else:
                    override_replicas_new[possible_clusters[i]] = int(list(override_replicas.values())[i]) + int((cloud_replicas / len(possible_clusters)))

            for cluster in possible_clusters:
                replicas = int(override_replicas_new[cluster])
                # is_eligible = checkClusterEligibility(cluster, app_cpu_request, app_memory_request, replicas)
                # The maximum number of replicas the cluster can host
                maximum_replicas = getMaximumReplicas(cluster, fogapp_cpu_request, fogapp_memory_request)
                #maximum_replicas = getAllocatableCapacity(cluster, fogapp_cpu_request, fogapp_memory_request)
                if maximum_replicas > replicas:
                    dict = {}
                    dict['name'] = cluster
                    dict['max_replicas'] = maximum_replicas
                    dict['replicas'] = replicas
                    dict['overflow'] = 0
                    eligible_clusters.append(dict)
                else:
                    dict = {}
                    dict['name'] = cluster
                    dict['max_replicas'] = maximum_replicas
                    dict['replicas'] = maximum_replicas
                    dict['overflow'] = replicas - maximum_replicas
                    eligible_clusters.append(dict)

            temp_list = []
            for cluster in eligible_clusters:
                temp_list.append(cluster)
            print("Possible list of clusters and oveflow ....", temp_list)

            temp_list_2 = []
            for cluster in temp_list:
                temp_list_2.append(cluster['name'])

            temp_list_3 = list(set(fogapp_locations + temp_list_2))

            total_overflow = 0

            for cluster in temp_list:
                total_overflow += cluster['overflow']

            maximum_replicas = {}

            for cluster in temp_list:
                nearest_clusters = []
                overflow = cluster['overflow']
                #leftover = overflow
                print("Overflow from ", cluster, overflow)

                if overflow > 0:
                    nearest_clusters = findNearestClusters(cluster, temp_list_3)
                    print("List of nearest clusters ....", nearest_clusters)

                # Distribute overflow to nearest clusters
                if len(nearest_clusters) > 0:
                    for c in nearest_clusters:
                        # print("Overflow .................", overflow)
                        # if overflow > 0:
                        maximum_replicas[c] = getMaximumReplicas(c, fogapp_cpu_request, fogapp_memory_request)
                        print("Maximum replicas .....", maximum_replicas)

            for cluster in temp_list:
                nearest_clusters = []
                overflow = cluster['overflow']
                if overflow > 0:
                    nearest_clusters = findNearestClusters(cluster, temp_list_3)
                # else:
                #     break
                if len(nearest_clusters) > 0:
                    for c in nearest_clusters:
                        if cluster['overflow'] > 0:
                            if maximum_replicas[c] == 0:
                                cluster['overflow'] = cluster['overflow']
                                #break
                            elif maximum_replicas[c] > cluster['overflow']:
                                dict = {}
                                dict['name'] = c
                                dict['replicas'] = cluster['overflow']
                                dict['overflow'] = 0
                                eligible_clusters.append(dict)
                                maximum_replicas[c] = maximum_replicas[c] - cluster['overflow']
                                cluster['overflow'] = 0
                                #break
                            else:
                                dict = {}
                                dict['name'] = c
                                dict['replicas'] = maximum_replicas[c]
                                dict['overflow'] = 0
                                cluster['overflow'] = cluster['overflow'] - maximum_replicas[c]
                                eligible_clusters.append(dict)
                                maximum_replicas[c] = 0

            # Group clusters and replicas
            eligible_clusters = (pd.DataFrame(eligible_clusters)
                                 .groupby(['name'], as_index=False)
                                 .agg({'replicas': 'sum', 'overflow': 'sum'})
                                 .to_dict('r'))

            print("Preliminary list of eligible clusters ...", eligible_clusters)

            # Compute leftover to be deployed on cloud cluster
            leftover = 0

            for cluster in eligible_clusters:
                if cluster['overflow'] > 0:
                    leftover += cluster['overflow']

            if leftover > 0:
                for cluster in fogapp_locations:
                    if 'cloud' in cluster:
                        dict = {}
                        dict['name'] = cluster
                        dict['replicas'] = leftover
                        dict['overflow'] = 0
                        eligible_clusters.append(dict)
                        leftover = 0
                        print("Eligible clusters including cloud ...........", eligible_clusters)

            if len(eligible_clusters) == 0:
                dict = {}
                dict['message'] = 'to_cloud'
                dict['replicas'] = total_replicas
                patch.status['message'] = dict
                raise kopf.TemporaryError(
                    "The application could not be scheduled on the Fog elevel. Need cloud cluster.",
                    delay=30)
            else:
                if leftover > 0:
                    cloud_cluster = getCloudCluster()
                    if 'cloud' in cloud_cluster:
                        dict = {}
                        dict['name'] = cloud_cluster
                        dict['replicas'] = leftover
                        dict['overflow'] = 0
                        eligible_clusters.append(dict)
                        leftover = 0
                        print("Eligible clusters including cloud ...........", eligible_clusters)
                    else:
                        dict = {}
                        dict['message'] = 'to_cloud'
                        dict['replicas'] = leftover
                        patch.status['message'] = dict
                        raise kopf.TemporaryError(
                            "The application could not be scheduled on the Fog level. Need cloud cluster.",
                            delay=30)

    for cluster in eligible_clusters:
        if cluster['replicas'] == 0:
            eligible_clusters.remove(cluster)

    print("Final list of eligible clusters ...", eligible_clusters)

    temp_list = []
    for cluster in eligible_clusters:
        temp_list.append(cluster)

    eligible_clusters = []
    eligible_replicas = []

    print("Jobs temp list ,,,,,,,,,,,,,,,,,,", temp_list)

    for cluster in temp_list:
        eligible_clusters.append(cluster['name'])
        eligible_replicas.append(cluster['replicas'])

    # For the spec file
    job_template = "{'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': '" + fogapp_name + "'}, 'spec': "
    job_json = job_template + spec_text + "}"
    job_text = job_json.replace("'", "\"")
    job_body = json.loads(job_text)


    #i = 0

    for cluster in eligible_clusters:
        # Update replicas per cluster
        #job_body['spec']['replicas'] = eligible_replicas[i]

        createJob(cluster, job_body, fogpapp_namespace)

    #    i += 1

    dict = {}
    dict['message'] = 'provisioned'
    dict['replicas'] = eligible_replicas
    patch.status['message'] = dict

    # TO DO: per cluster overrides
    return {'fogapp_name': fogapp_name, 'fogapp_namespace': fogpapp_namespace, 'input_clusters': fogapp_locations, 'input_replicas': fogapp_replicas, 'fogapp_replicas': eligible_replicas, 'fogapp_locations': eligible_clusters, 'fogapp_status': 'provisioned'}

@kopf.on.delete('fogguru.eu', 'v1', 'multiclusterjobs')
def delete(spec, body, status, **kwargs):
    fogapp_name = body['metadata']['name']

    # Get namespace
    if 'namespace' in body['metadata']:
        fogpapp_namespace = body['metadata']['namespace']
    else:
        fogpapp_namespace = "default"

    fogapp_locations_update = []
    fogapp_locations_create = []

    if 'update_fn' in status:
        fogapp_locations_update = status['update_fn']['fogapp_locations']
    if 'create_fn' in status:
        fogapp_locations_create = status['create_fn']['fogapp_locations']

    for cluster in fogapp_locations_update:
        try:
            deleteJob(cluster, fogapp_name, fogpapp_namespace)
        except:
            pass

    for cluster in fogapp_locations_create:
        try:
            deleteJob(cluster, fogapp_name, fogpapp_namespace)
        except:
            pass

    msg = f"Multi Cluster Job {fogapp_name} is DELETED!"
    return {'message': msg}
