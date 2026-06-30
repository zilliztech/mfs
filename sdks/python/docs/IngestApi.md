# mfs_sdk.IngestApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**add_source**](IngestApi.md#add_source) | **POST** /v1/add | Add
[**cancel_job**](IngestApi.md#cancel_job) | **POST** /v1/jobs/{job_id}/cancel | Cancel Job
[**files_manifest**](IngestApi.md#files_manifest) | **POST** /v1/files/manifest | Files Manifest
[**files_upload**](IngestApi.md#files_upload) | **PUT** /v1/files/upload | Files Upload
[**get_job**](IngestApi.md#get_job) | **GET** /v1/jobs/{job_id} | Job
[**list_jobs**](IngestApi.md#list_jobs) | **GET** /v1/jobs | List Jobs
[**upload_source**](IngestApi.md#upload_source) | **POST** /v1/upload | Upload


# **add_source**
> AddResponse add_source(add_request)

Add

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.add_request import AddRequest
from mfs_sdk.models.add_response import AddResponse
from mfs_sdk.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = mfs_sdk.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization (opaque): BearerAuth
configuration = mfs_sdk.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with mfs_sdk.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = mfs_sdk.IngestApi(api_client)
    add_request = mfs_sdk.AddRequest() # AddRequest | 

    try:
        # Add
        api_response = api_instance.add_source(add_request)
        print("The response of IngestApi->add_source:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling IngestApi->add_source: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **add_request** | [**AddRequest**](AddRequest.md)|  | 

### Return type

[**AddResponse**](AddResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |
**400** | Bad Request |  -  |
**401** | Unauthorized |  -  |
**404** | Not Found |  -  |
**405** | Method Not Allowed |  -  |
**500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **cancel_job**
> CancelResponse cancel_job(job_id)

Cancel Job

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.cancel_response import CancelResponse
from mfs_sdk.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = mfs_sdk.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization (opaque): BearerAuth
configuration = mfs_sdk.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with mfs_sdk.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = mfs_sdk.IngestApi(api_client)
    job_id = 'job_id_example' # str | 

    try:
        # Cancel Job
        api_response = api_instance.cancel_job(job_id)
        print("The response of IngestApi->cancel_job:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling IngestApi->cancel_job: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **job_id** | **str**|  | 

### Return type

[**CancelResponse**](CancelResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |
**400** | Bad Request |  -  |
**401** | Unauthorized |  -  |
**404** | Not Found |  -  |
**405** | Method Not Allowed |  -  |
**500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **files_manifest**
> ManifestResponse files_manifest(manifest_request)

Files Manifest

Manifest-diff upload step ②: stat-only manifest in, need_sha1 + deletion
candidates out. No bytes transferred here.

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.manifest_request import ManifestRequest
from mfs_sdk.models.manifest_response import ManifestResponse
from mfs_sdk.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = mfs_sdk.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization (opaque): BearerAuth
configuration = mfs_sdk.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with mfs_sdk.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = mfs_sdk.IngestApi(api_client)
    manifest_request = mfs_sdk.ManifestRequest() # ManifestRequest | 

    try:
        # Files Manifest
        api_response = api_instance.files_manifest(manifest_request)
        print("The response of IngestApi->files_manifest:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling IngestApi->files_manifest: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **manifest_request** | [**ManifestRequest**](ManifestRequest.md)|  | 

### Return type

[**ManifestResponse**](ManifestResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |
**400** | Bad Request |  -  |
**401** | Unauthorized |  -  |
**404** | Not Found |  -  |
**405** | Method Not Allowed |  -  |
**500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **files_upload**
> AddResponse files_upload(client_id, root, process=process, full=full)

Files Upload

Manifest-diff upload step ④: PUT a tar(.gz) carrying a `.mfs-meta.json`
member (hashes/renames/deletions) + the changed file bytes. The server applies
it to the staging area and triggers the file-connector sync. full=true
(--force-index/--force-upload) forces a re-index of the whole staged tree.

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.add_response import AddResponse
from mfs_sdk.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = mfs_sdk.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization (opaque): BearerAuth
configuration = mfs_sdk.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with mfs_sdk.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = mfs_sdk.IngestApi(api_client)
    client_id = 'client_id_example' # str | 
    root = 'root_example' # str | 
    process = True # bool |  (optional) (default to True)
    full = False # bool |  (optional) (default to False)

    try:
        # Files Upload
        api_response = api_instance.files_upload(client_id, root, process=process, full=full)
        print("The response of IngestApi->files_upload:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling IngestApi->files_upload: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **client_id** | **str**|  | 
 **root** | **str**|  | 
 **process** | **bool**|  | [optional] [default to True]
 **full** | **bool**|  | [optional] [default to False]

### Return type

[**AddResponse**](AddResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |
**400** | Bad Request |  -  |
**401** | Unauthorized |  -  |
**404** | Not Found |  -  |
**405** | Method Not Allowed |  -  |
**500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_job**
> JobResponse get_job(job_id)

Job

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.job_response import JobResponse
from mfs_sdk.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = mfs_sdk.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization (opaque): BearerAuth
configuration = mfs_sdk.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with mfs_sdk.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = mfs_sdk.IngestApi(api_client)
    job_id = 'job_id_example' # str | 

    try:
        # Job
        api_response = api_instance.get_job(job_id)
        print("The response of IngestApi->get_job:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling IngestApi->get_job: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **job_id** | **str**|  | 

### Return type

[**JobResponse**](JobResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |
**400** | Bad Request |  -  |
**401** | Unauthorized |  -  |
**404** | Not Found |  -  |
**405** | Method Not Allowed |  -  |
**500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **list_jobs**
> List[JobResponse] list_jobs(limit=limit)

List Jobs

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.job_response import JobResponse
from mfs_sdk.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = mfs_sdk.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization (opaque): BearerAuth
configuration = mfs_sdk.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with mfs_sdk.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = mfs_sdk.IngestApi(api_client)
    limit = 20 # int |  (optional) (default to 20)

    try:
        # List Jobs
        api_response = api_instance.list_jobs(limit=limit)
        print("The response of IngestApi->list_jobs:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling IngestApi->list_jobs: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **limit** | **int**|  | [optional] [default to 20]

### Return type

[**List[JobResponse]**](JobResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |
**400** | Bad Request |  -  |
**401** | Unauthorized |  -  |
**404** | Not Found |  -  |
**405** | Method Not Allowed |  -  |
**500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **upload_source**
> AddResponse upload_source(name, process=process)

Upload

CS upload flow: POST a tar(.gz) of a tree as the raw body (?name=<label>);
the server stages + indexes it. For client/server without a shared filesystem.

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.add_response import AddResponse
from mfs_sdk.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = mfs_sdk.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization (opaque): BearerAuth
configuration = mfs_sdk.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with mfs_sdk.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = mfs_sdk.IngestApi(api_client)
    name = 'name_example' # str | 
    process = True # bool |  (optional) (default to True)

    try:
        # Upload
        api_response = api_instance.upload_source(name, process=process)
        print("The response of IngestApi->upload_source:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling IngestApi->upload_source: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **name** | **str**|  | 
 **process** | **bool**|  | [optional] [default to True]

### Return type

[**AddResponse**](AddResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |
**400** | Bad Request |  -  |
**401** | Unauthorized |  -  |
**404** | Not Found |  -  |
**405** | Method Not Allowed |  -  |
**500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

