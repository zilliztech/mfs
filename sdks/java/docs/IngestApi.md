# IngestApi

All URIs are relative to *http://127.0.0.1:8765*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**addSource**](IngestApi.md#addSource) | **POST** /v1/add | Add |
| [**cancelJob**](IngestApi.md#cancelJob) | **POST** /v1/jobs/{job_id}/cancel | Cancel Job |
| [**getJob**](IngestApi.md#getJob) | **GET** /v1/jobs/{job_id} | Job |
| [**uploadSource**](IngestApi.md#uploadSource) | **POST** /v1/upload | Upload |


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

<a id="cancelJob"></a>
# **cancelJob**
> CancelResponse cancelJob(jobId)

Cancel Job

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
      CancelResponse result = apiInstance.cancelJob(jobId);
      System.out.println(result);
    } catch (ApiException e) {
      System.err.println("Exception when calling IngestApi#cancelJob");
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

[**CancelResponse**](CancelResponse.md)

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

<a id="uploadSource"></a>
# **uploadSource**
> AddResponse uploadSource(name, process)

Upload

CS upload flow: POST a tar(.gz) of a tree as the raw body (?name&#x3D;&lt;label&gt;); the server stages + indexes it. For client/server without a shared filesystem.

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
    String name = "name_example"; // String | 
    Boolean process = true; // Boolean | 
    try {
      AddResponse result = apiInstance.uploadSource(name, process);
      System.out.println(result);
    } catch (ApiException e) {
      System.err.println("Exception when calling IngestApi#uploadSource");
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
| **name** | **String**|  | |
| **process** | **Boolean**|  | [optional] [default to true] |

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
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

