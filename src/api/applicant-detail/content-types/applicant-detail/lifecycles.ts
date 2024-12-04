import Strapi from '@strapi/strapi';
import axios from 'axios';

export default {
    async afterCreate(event) {
        const {result, params} = event;
        const globalSettings = await strapi.entityService.findOne('api::global-setting.global-setting',1,{
            fields: ['webhook_url']
        });
        const webhook_url = globalSettings?.webhook_url;

        if (!webhook_url) {
            console.error('WebhookURL is not defined in Global Settings');
            return;
        }

        // strapi.service[]

        axios.post(webhook_url, result, {
            headers: {
                'Content-Type': 'application/json',
            },
        });
        
    }
}