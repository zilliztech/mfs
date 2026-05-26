# ServerApi

All URIs are relative to *http://127.0.0.1:8765*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**getServerInfo**](ServerApi.md#getServerInfo) | **GET** /v1/server/info | Server Info |
| [**status**](ServerApi.md#status) | **GET** /v1/status | Status |


<a id="getServerInfo"></a>
# **getServerInfo**
> ServerInfo getServerInfo()

Server Info

### Example
```java
// Import classes:
import io.zilliz.mfs.ApiClient;
import io.zilliz.mfs.ApiException;
import io.zilliz.mfs.Configuration;
import io.zilliz.mfs.models.*;
import io.zilliz.mfs.api.ServerApi;

public class Example {
  public static void main(String[] args) {
    ApiClient defaultClient = Configuration.getDefaultApiClient();
    defaultClient.setBasePath("http://127.0.0.1:8765");

    ServerApi apiInstance = new ServerApi(defaultClient);
    try {
      ServerInfo result = apiInstance.getServerInfo();
      System.out.println(result);
    } catch (ApiException e) {
      System.err.println("Exception when calling ServerApi#getServerInfo");
      System.err.println("Status code: " + e.getCode());
      System.err.println("Reason: " + e.getResponseBody());
      System.err.println("Response headers: " + e.getResponseHeaders());
      e.printStackTrace();
    }
  }
}
```

### Parameters
This endpoint does not need any parameter.

### Return type

[**ServerInfo**](ServerInfo.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |

<a id="status"></a>
# **status**
> StatusResponse status()

Status

### Example
```java
// Import classes:
import io.zilliz.mfs.ApiClient;
import io.zilliz.mfs.ApiException;
import io.zilliz.mfs.Configuration;
import io.zilliz.mfs.models.*;
import io.zilliz.mfs.api.ServerApi;

public class Example {
  public static void main(String[] args) {
    ApiClient defaultClient = Configuration.getDefaultApiClient();
    defaultClient.setBasePath("http://127.0.0.1:8765");

    ServerApi apiInstance = new ServerApi(defaultClient);
    try {
      StatusResponse result = apiInstance.status();
      System.out.println(result);
    } catch (ApiException e) {
      System.err.println("Exception when calling ServerApi#status");
      System.err.println("Status code: " + e.getCode());
      System.err.println("Reason: " + e.getResponseBody());
      System.err.println("Response headers: " + e.getResponseHeaders());
      e.printStackTrace();
    }
  }
}
```

### Parameters
This endpoint does not need any parameter.

### Return type

[**StatusResponse**](StatusResponse.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |

