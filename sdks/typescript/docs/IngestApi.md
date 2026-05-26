# IngestApi

All URIs are relative to *http://127.0.0.1:8765*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**addSource**](IngestApi.md#addsource) | **POST** /v1/add | Add |
| [**getJob**](IngestApi.md#getjob) | **GET** /v1/jobs/{job_id} | Job |



## addSource

> AddResponse addSource(addRequest)

Add

### Example

```ts
import {
  Configuration,
  IngestApi,
} from '@mfs/sdk';
import type { AddSourceRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const api = new IngestApi();

  const body = {
    // AddRequest
    addRequest: ...,
  } satisfies AddSourceRequest;

  try {
    const data = await api.addSource(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **addRequest** | [AddRequest](AddRequest.md) |  | |

### Return type

[**AddResponse**](AddResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: `application/json`
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## getJob

> JobResponse getJob(jobId)

Job

### Example

```ts
import {
  Configuration,
  IngestApi,
} from '@mfs/sdk';
import type { GetJobRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const api = new IngestApi();

  const body = {
    // string
    jobId: jobId_example,
  } satisfies GetJobRequest;

  try {
    const data = await api.getJob(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **jobId** | `string` |  | [Defaults to `undefined`] |

### Return type

[**JobResponse**](JobResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)

