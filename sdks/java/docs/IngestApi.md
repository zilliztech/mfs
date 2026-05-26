# IngestApi

All URIs are relative to *http://127.0.0.1:8765*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**addSource**](IngestApi.md#addSource) | **POST** /v1/add | Add |
| [**getJob**](IngestApi.md#getJob) | **GET** /v1/jobs/{job_id} | Job |


<a id="addSource"></a>
# **addSource**
> AddResponse addSource(addRequest)

Add

### Example
```java
// Import classes:
import io.zilliz.mfs.ApiClient;
import io.zilliz.mfs.ApiException;
import io.zilliz.mfs.Configuration;
import io.zilliz.mfs.models.*;
import io.zilliz.mfs.api.IngestApi;

public class Example {
  public static void main(String[] args) {
    ApiClient defaultClient = Configuration.getDefaultApiClient();
    defaultClient.setBasePath("http://127.0.0.1:8765");

    IngestApi apiInstance = new IngestApi(defaultClient);
    AddRequest addRequest = new AddRequest(); // AddRequest | 
    try {
      AddResponse result = apiInstance.addSource(addRequest);
      System.out.println(result);
    } catch (ApiException e) {
      System.err.println("Exception when calling IngestApi#addSource");
      System.err.println("Status code: " + e.getCode());
      System.err.println("Reason: " + e.getResponseBody());
      System.err.println("Response headers: " + e.getResponseHeaders());
      e.printStackTrace();
    }
  }
}
```

### Parameters

| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **addRequest** | [**AddRequest**](AddRequest.md)|  | |

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
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

<a id="getJob"></a>
# **getJob**
> JobResponse getJob(jobId)

Job

### Example
```java
// Import classes:
import io.zilliz.mfs.ApiClient;
import io.zilliz.mfs.ApiException;
import io.zilliz.mfs.Configuration;
import io.zilliz.mfs.models.*;
import io.zilliz.mfs.api.IngestApi;

public class Example {
  public static void main(String[] args) {
    ApiClient defaultClient = Configuration.getDefaultApiClient();
    defaultClient.setBasePath("http://127.0.0.1:8765");

    IngestApi apiInstance = new IngestApi(defaultClient);
    String jobId = "jobId_example"; // String | 
    try {
      JobResponse result = apiInstance.getJob(jobId);
      System.out.println(result);
    } catch (ApiException e) {
      System.err.println("Exception when calling IngestApi#getJob");
      System.err.println("Status code: " + e.getCode());
      System.err.println("Reason: " + e.getResponseBody());
      System.err.println("Response headers: " + e.getResponseHeaders());
      e.printStackTrace();
    }
  }
}
```

### Parameters

| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **jobId** | **String**|  | |

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
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

