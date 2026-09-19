package com.edge.app.config;

import org.springframework.beans.factory.support.BeanDefinitionRegistryPostProcessor;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.List;

@Configuration
@ConditionalOnProperty(name = "vote.mode", havingValue = "write-behind")
public class WriteBehindConfig {
    static final List<String> DB_FIRST_BEANS = List.of("voteReconciler", "voteAdminController");

    @Bean
    static BeanDefinitionRegistryPostProcessor dropDbFirstBeans() {
        return registry -> DB_FIRST_BEANS.stream().filter(registry::containsBeanDefinition)
                .forEach(registry::removeBeanDefinition);
    }
}
