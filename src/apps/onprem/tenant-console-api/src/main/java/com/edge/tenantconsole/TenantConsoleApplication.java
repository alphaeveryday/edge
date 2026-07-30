package com.edge.tenantconsole;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

@ConfigurationPropertiesScan
@SpringBootApplication
public class TenantConsoleApplication {

	public static void main(String[] args) {
		SpringApplication.run(TenantConsoleApplication.class, args);
	}

}
