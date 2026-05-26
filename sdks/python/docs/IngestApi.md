# mfs_sdk.IngestApi

All URIs are relative to *http://127.0.0.1:8765*

Method | HTTP request | Description
------------- | ------------- | -------------
[**add_source**](IngestApi.md#add_source) | **POST** /v1/add | Add
[**cancel_job**](IngestApi.md#cancel_job) | **POST** /v1/jobs/{job_id}/cancel | Cancel Job
[**get_job**](IngestApi.md#get_job) | **GET** /v1/jobs/{job_id} | Job
[**upload_source**](IngestApi.md#upload_source) | **POST** /v1/upload | Upload


# **add_source**
> AddResponse add_source(add_request)

Add

### Example


```python
import mfs_sdk
from mfs_sdk.models.add_request import AddRequest
from mfs_sdk.models.add_response import AddResponse
from mfs_sdk.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://127.0.0.1:8765
# See configuration.py for a list of all supported configuration parameters.
configuration = mfs_sdk.Configuration(
    host = "http://127.0.0.1:8765"
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

No authorization required

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **cancel_job**
> CancelResponse cancel_job(job_id)

Cancel Job

### Example


```python
import mfs_sdk
from mfs_sdk.models.cancel_response import CancelResponse
from mfs_sdk.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://127.0.0.1:8765
# See configuration.py for a list of all supported configuration parameters.
configuration = mfs_sdk.Configuration(
    host = "http://127.0.0.1:8765"
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

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_job**
> JobResponse get_job(job_id)

Job

### Example


```python
import mfs_sdk
from mfs_sdk.models.job_response import JobResponse
from mfs_sdk.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://127.0.0.1:8765
# See configuration.py for a list of all supported configuration parameters.
configuration = mfs_sdk.Configuration(
    host = "http://127.0.0.1:8765"
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

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **upload_source**
> AddResponse upload_source(name, process=process)

Upload

CS upload flow: POST a tar(.gz) of a tree as the raw body (?name=<label>);
the server stages + indexes it. For client/server without a shared filesystem.

### Example


```python
import mfs_sdk
from mfs_sdk.models.add_response import AddResponse
from mfs_sdk.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://127.0.0.1:8765
# See configuration.py for a list of all supported configuration parameters.
configuration = mfs_sdk.Configuration(
    host = "http://127.0.0.1:8765"
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

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

